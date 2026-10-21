"""Pilotage du processus llama-server : lancement, surveillance, arrêt et journal."""

import asyncio
import re
import secrets
import subprocess
import sys
import threading
import time
from collections import deque

import httpx

from . import config, engines, jobs

# Messages de llama.cpp officiel quand un GGUF utilise un type de tenseur qu'il ne connaît pas
UNKNOWN_TYPE_RE = re.compile(r"invalid ggml type|unknown (?:ggml )?type", re.IGNORECASE)

# Clé API régénérée à chaque lancement : passée par fichier pour ne pas apparaître
# dans le journal ni dans la ligne de commande du processus
KEY_PATH = config.ROOT / "data" / "llama.key"


class EngineMissing(Exception):
    pass


def build_args(model, settings, port, app_port):
    args = [
        "-m", model.path,
        "--host", "127.0.0.1",
        "--port", str(port),
        "--api-key-file", str(KEY_PATH),
        "--cors-origins", f"http://127.0.0.1:{app_port}",
        "-c", str(settings["ctx_size"]),
        "-ngl", str(settings["gpu_layers"]),
        "--alias", model.name,
        "--jinja",
    ]
    if settings.get("threads"):
        args += ["-t", str(settings["threads"])]
    if settings.get("flash_attn") in ("on", "off"):
        args += ["-fa", settings["flash_attn"]]
    if settings.get("use_mmproj") and model.mmproj:
        args += ["--mmproj", model.mmproj]
    return args


class ServerManager:
    def __init__(self):
        self.proc = None
        self.state = "idle"  # idle | loading | ready | error
        self.error = None
        self.model = None
        self.engine = None
        self.started_at = None
        self.api_key = None
        self._settings = None
        self._auto = False
        self._monitor = None
        self._lock = asyncio.Lock()
        self._log_lock = threading.Lock()
        self._logs = deque(maxlen=2000)
        self._log_seq = 0

    # --- journal -------------------------------------------------------------

    def _log(self, line):
        with self._log_lock:
            self._log_seq += 1
            self._logs.append((self._log_seq, line.rstrip("\r\n")))

    def logs_since(self, seq):
        with self._log_lock:
            return {"seq": self._log_seq, "lines": [l for s, l in self._logs if s > seq]}

    def _recent_logs(self, n=40):
        with self._log_lock:
            return [l for _, l in list(self._logs)[-n:]]

    # --- cycle de vie --------------------------------------------------------

    def auth_headers(self):
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def status(self):
        port = config.get()["llama_port"]
        return {
            "state": self.state,
            "error": self.error,
            "model": {"id": self.model.id, "name": self.model.name} if self.model else None,
            "engine": self.engine,
            "pid": self.proc.pid if self.proc and self.proc.poll() is None else None,
            "api_url": f"http://127.0.0.1:{port}/v1" if self.state == "ready" else None,
            "uptime_s": int(time.time() - self.started_at) if self.started_at and self.state == "ready" else None,
        }

    async def load(self, model, settings, engine, auto):
        async with self._lock:
            await self._stop()
            self.model, self._settings, self._auto = model, settings, auto
            self._spawn(engine)
            self._monitor = asyncio.create_task(self._watch())

    async def unload(self):
        async with self._lock:
            await self._stop()
            self._log("[aria] modèle éjecté")

    async def shutdown(self):
        await self._stop()

    def _spawn(self, engine):
        exe = engines.find_server(engine)
        if exe is None:
            self.state = "error"
            self.error = f"Moteur « {engine} » non installé (lance tools/install_engines.py)."
            raise EngineMissing(self.error)
        cfg = config.get()
        self.api_key = secrets.token_urlsafe(32)
        KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
        KEY_PATH.write_text(self.api_key + "\n", encoding="utf-8")
        args = build_args(self.model, self._settings, cfg["llama_port"], cfg["app_port"])
        self._log(f"[aria] lancement ({engine}) : {exe.name} {subprocess.list2cmdline(args)}")
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self.proc = subprocess.Popen(
            [str(exe), *args],
            cwd=exe.parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=flags,
        )
        jobs.attach(self.proc)
        threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()
        self.engine = engine
        self.state = "loading"
        self.error = None
        self.started_at = time.time()

    def _pump(self, proc):
        for line in proc.stdout:
            self._log(line)

    async def _watch(self):
        port = config.get()["llama_port"]
        async with httpx.AsyncClient(timeout=2) as client:
            while True:
                await asyncio.sleep(0.5)
                code = self.proc.poll()
                if code is not None:
                    if self._should_fallback():
                        self._log("[aria] type de tenseur inconnu pour llama.cpp officiel → bascule sur PrismML")
                        config.save_model_settings(self.model.path, {"engine": "prism"})
                        self._spawn("prism")
                        continue
                    self.state = "error"
                    self.error = f"llama-server s'est arrêté (code {code}). Regarde le journal."
                    self._log(f"[aria] {self.error}")
                    return
                if self.state == "loading":
                    try:
                        r = await client.get(f"http://127.0.0.1:{port}/health", headers=self.auth_headers())
                        if r.status_code == 200:
                            self.state = "ready"
                            self._log(f"[aria] prêt : API sur http://127.0.0.1:{port}/v1")
                    except httpx.HTTPError:
                        pass

    def _should_fallback(self):
        if not (self._auto and self.engine == "llama.cpp" and self.state == "loading"):
            return False
        if engines.find_server("prism") is None:
            return False
        return any(UNKNOWN_TYPE_RE.search(l) for l in self._recent_logs())

    async def _stop(self):
        if self._monitor:
            self._monitor.cancel()
            self._monitor = None
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                await asyncio.to_thread(self.proc.wait, 10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        self.state = "idle"
        self.error = None
        self.model = None
        self.engine = None
        self.started_at = None
        self.api_key = None
        if KEY_PATH.exists():
            KEY_PATH.unlink()


manager = ServerManager()
