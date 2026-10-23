"""SearXNG embarqué : démarré à la demande par Aria Pilote, arrêté avec elle."""

import asyncio
import json
import os
import subprocess
import sys
import time

import httpx

from . import config, jobs

SERVICE_DIR = config.ROOT / "services" / "searxng"
VENV_PYTHON = SERVICE_DIR / ".venv" / "Scripts" / "python.exe"
PORTABLE_PACKAGES = SERVICE_DIR / "site-packages"
SRC_DIR = SERVICE_DIR / "src"
SETTINGS_PATH = SERVICE_DIR / "settings.yml"
LOG_PATH = config.ROOT / "data" / "searxng.log"
START_TIMEOUT_S = 60
IDLE_CHECK_S = 30


class SearxManager:
    last_used = 0.0

    def __init__(self):
        self.proc = None
        self._lock = asyncio.Lock()

    @staticmethod
    def installed():
        has_python = PORTABLE_PACKAGES.is_dir() or VENV_PYTHON.exists()
        return has_python and SETTINGS_PATH.exists() and (SRC_DIR / "searx" / "webapp.py").exists()

    @staticmethod
    def _command():
        """Python à utiliser et environnement : site-packages à part (portable) ou venv (développement)."""
        env = {**os.environ, "SEARXNG_SETTINGS_PATH": str(SETTINGS_PATH), "PYTHONIOENCODING": "utf-8", "PYTHONNOUSERSITE": "1"}
        if PORTABLE_PACKAGES.is_dir():
            env["PYTHONPATH"] = os.pathsep.join([str(PORTABLE_PACKAGES), str(SRC_DIR)])
            return sys.executable, env
        return str(VENV_PYTHON), env

    @staticmethod
    def url():
        return config.get()["searxng_url"].rstrip("/")

    async def healthy(self):
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                return (await client.get(f"{self.url()}/healthz")).status_code == 200
        except httpx.HTTPError:
            return False

    async def ensure_running(self):
        """Démarre SearXNG s'il est installé et ne répond pas déjà (un SearXNG externe est aussi accepté)."""
        self.last_used = time.monotonic()
        async with self._lock:
            if await self.healthy():
                return
            if not self.installed():
                return
            if self.proc is None or self.proc.poll() is not None:
                LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
                python, env = self._command()
                log = open(LOG_PATH, "w", encoding="utf-8")
                self.proc = subprocess.Popen(
                    [python, "-m", "searx.webapp"],
                    cwd=SRC_DIR,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                )
                jobs.attach(self.proc)
                log.close()
            deadline = time.monotonic() + START_TIMEOUT_S
            while time.monotonic() < deadline:
                if self.proc.poll() is not None:
                    return  # planté : l'erreur « injoignable » renverra au journal
                if await self.healthy():
                    return
                await asyncio.sleep(0.5)

    async def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                await asyncio.to_thread(self.proc.wait, 10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def info(self):
        release = None
        manifest = SERVICE_DIR / "service.json"
        if manifest.exists():
            data = json.loads(manifest.read_text(encoding="utf-8"))
            release = {"tag": data["commit"][:7], "backend": "en marche" if self.running() else "à la demande"}
        return {
            "name": "searxng",
            "label": "SearXNG (recherche web)",
            "installed": self.installed(),
            "path": str(SERVICE_DIR) if self.installed() else None,
            "release": release,
        }

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def idle_expired(self, minutes, now=None):
        """Lancé par Aria, sans recherche depuis `minutes` minutes (0 : jamais)."""
        if not minutes or not self.running():
            return False
        return (now or time.monotonic()) - self.last_used > minutes * 60

    async def watch_idle(self):
        """Tourne avec l'appli : arrête SearXNG après l'inactivité choisie dans les réglages."""
        while True:
            await asyncio.sleep(IDLE_CHECK_S)
            if self.idle_expired(config.get().get("searxng_idle_minutes", 0)):
                await self.stop()

    def status(self):
        url = self.url()
        return {"installed": self.installed(), "running": self.running(), "url": url,
                "port": url.rsplit(":", 1)[-1], "idle_minutes": config.get().get("searxng_idle_minutes", 0)}


manager = SearxManager()
