"""Terminal intégré : un vrai PowerShell (pseudo-terminal ConPTY, via pywinpty) par conversation, dans son dossier
de travail, affiché par xterm.js dans le panneau latéral et relié par un WebSocket.

Un WebSocket ne passe pas par le contrôle d'accès des requêtes HTTP (main.local_only) : n'importe quelle page ouverte
dans le navigateur peut tenter de s'y connecter, et obtenir un shell. Trois verrous, tous vérifiés avant d'accepter :
- l'hôte, contre le « DNS rebinding » ;
- l'origine, obligatoire ici, et qui doit être l'interface d'Aria Pilote ;
- un jeton à usage unique, que seule l'interface peut obtenir (par un POST, lui-même protégé).
"""

import secrets
import threading
import time
from collections import deque

from . import config, jobs
from .tools import POWERSHELL

TOKEN_TTL_S = 30
BUFFER_CHARS = 200_000  # ce qui est réaffiché après un rechargement de la page
DEFAULT_SIZE = (24, 80)  # lignes, colonnes
MIN_SIZE, MAX_SIZE = (2, 10), (500, 1000)


class TerminalError(Exception):
    pass


# --- Contrôle d'accès du WebSocket ---------------------------------------------------------------

_tokens = {}  # jeton -> (conversation, échéance)
_tokens_lock = threading.Lock()


def issue_token(cid):
    """Jeton à usage unique pour ouvrir le terminal de cette conversation."""
    now = time.monotonic()
    with _tokens_lock:
        for token in [t for t, (_, deadline) in _tokens.items() if deadline < now]:
            del _tokens[token]
        token = secrets.token_urlsafe(32)
        _tokens[token] = (cid, now + TOKEN_TTL_S)
    return token


def redeem_token(token):
    """La conversation du jeton, qui ne resservira pas ; None s'il est inconnu, déjà utilisé ou périmé."""
    with _tokens_lock:
        cid, deadline = _tokens.pop(token or "", (None, 0))
    return cid if cid and deadline >= time.monotonic() else None


def allowed_hosts():
    port = config.get()["app_port"]
    return {f"127.0.0.1:{port}", f"localhost:{port}"}


def refusal(headers):
    """Pourquoi cette demande de WebSocket est refusée (None : l'hôte et l'origine sont ceux de l'interface)."""
    hosts = allowed_hosts()
    if headers.get("host") not in hosts:
        return "Hôte refusé."
    # Un navigateur envoie toujours l'origine d'un WebSocket : son absence n'est pas une preuve d'innocence
    if headers.get("origin") not in {f"http://{h}" for h in hosts}:
        return "Origine refusée."
    return None


# --- Sessions ------------------------------------------------------------------------------------

class Session:
    """Un PowerShell dans un pseudo-terminal : sa sortie est gardée en tampon et diffusée aux pages connectées."""

    def __init__(self, cid, cwd, size=DEFAULT_SIZE):
        from winpty import PtyProcess  # Windows seulement : importé à l'usage

        self.cid, self.cwd = cid, str(cwd)
        self.size = _clamp(size)
        self._buffer, self._buffered = deque(), 0
        self._listeners = set()
        self._lock = threading.Lock()
        # -NoLogo : pas de bannière. L'UTF-8 est posé d'emblée pour les programmes lancés depuis ce terminal.
        argv = [POWERSHELL, "-NoLogo", "-NoExit", "-Command",
                "[Console]::OutputEncoding = [Console]::InputEncoding = [System.Text.Encoding]::UTF8"]
        # Lancée sans console (pythonw, tâche de fond), Aria ignore Ctrl+C, et ses enfants en héritent : sans cette
        # ligne, Ctrl+C n'arrête pas un programme lancé dans le terminal (un ping -t, par exemple).
        import ctypes
        ctypes.windll.kernel32.SetConsoleCtrlHandler(None, False)
        self.proc = PtyProcess.spawn(argv, cwd=self.cwd, dimensions=self.size, env=_environment())
        self.pid = self.proc.pid
        jobs.attach_pid(self.pid)  # tué avec Aria, même si elle meurt brutalement
        threading.Thread(target=self._read, daemon=True, name=f"terminal-{cid}").start()

    def _read(self):
        while True:
            try:
                data = self.proc.read(4096)
            except (EOFError, OSError):
                break
            if not data:
                if not self.proc.isalive():
                    break
                continue
            with self._lock:
                self._buffer.append(data)
                self._buffered += len(data)
                while self._buffered > BUFFER_CHARS and len(self._buffer) > 1:
                    self._buffered -= len(self._buffer.popleft())
                listeners = list(self._listeners)
            for listener in listeners:
                listener(data)
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            listener(None)  # fin du processus

    def subscribe(self, listener):
        """Abonne `listener(texte | None)` et renvoie ce qui a déjà été affiché, sans rien perdre entre les deux."""
        with self._lock:
            self._listeners.add(listener)
            return "".join(self._buffer)

    def unsubscribe(self, listener):
        with self._lock:
            self._listeners.discard(listener)

    def alive(self):
        return self.proc.isalive()

    def write(self, text):
        if text and self.alive():
            self.proc.write(text)

    def resize(self, rows, cols):
        self.size = _clamp((rows, cols))
        if self.alive():
            self.proc.setwinsize(*self.size)

    def stop(self):
        if self.alive():
            jobs.kill_tree(self.pid)  # PowerShell et tout ce qu'il a lancé
        try:
            self.proc.close(force=True)
        except Exception:  # déjà fermé : rien à faire
            pass


def _clamp(size):
    try:
        rows, cols = int(size[0]), int(size[1])
    except (TypeError, ValueError, IndexError):
        return DEFAULT_SIZE
    return (max(MIN_SIZE[0], min(MAX_SIZE[0], rows)), max(MIN_SIZE[1], min(MAX_SIZE[1], cols)))


def _environment():
    import os
    return {**os.environ, **jobs.COMMAND_ENV, "TERM": "xterm-256color"}


_sessions = {}
_sessions_lock = threading.Lock()


def get(cid):
    with _sessions_lock:
        session = _sessions.get(cid)
    return session if session and session.alive() else None


def open_session(cid, cwd, size=DEFAULT_SIZE):
    """Le terminal de la conversation : celui qui tourne déjà, sinon un nouveau dans `cwd`."""
    with _sessions_lock:
        session = _sessions.get(cid)
        if session and session.alive():
            return session
        try:
            session = Session(cid, cwd, size)
        except Exception as exc:
            raise TerminalError(f"Terminal impossible à ouvrir : {exc}")
        _sessions[cid] = session
        return session


def close(cid):
    with _sessions_lock:
        session = _sessions.pop(cid, None)
    if session:
        session.stop()
    return session is not None


def stop_all():
    with _sessions_lock:
        sessions = list(_sessions.values())
        _sessions.clear()
    for session in sessions:
        session.stop()
