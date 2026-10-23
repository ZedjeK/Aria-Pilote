"""Commandes lancées en arrière-plan par l'agent : serveurs, watchers, longs builds.

L'agent lance la commande (avec ton accord, comme toute commande), lit sa sortie au fil de l'eau
puis l'arrête. À la fin de la tâche, ses commandes sont arrêtées, sauf celles que tu as demandé
de garder (keep) : elles restent listées dans la barre Agent jusqu'à ce que tu les arrêtes.
Toutes sont rattachées au job object : elles meurent avec Aria Pilote, même en cas de plantage.
"""

import codecs
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field

from . import jobs

MAX_PER_CONVERSATION = 3
MAX_FINISHED = 10       # commandes terminées gardées par conversation (sortie consultable dans le panneau)
PANEL_CHARS = 20_000    # fin de sortie montrée dans le panneau
# Codes de couleur des terminaux (ESC[…m) : illisibles dans le panneau
ANSI = re.compile(r"\[[0-9;?]*[A-Za-z]")
BUFFER_CHARS = 200_000  # on garde la fin de la sortie
START_WAIT_S = 3        # le temps d'attraper un plantage immédiat (port pris, fichier introuvable…)
MAX_WAIT_S = 30


class BackgroundError(Exception):
    pass


@dataclass
class Command:
    id: str
    cid: str
    task_id: str | None
    command: str
    keep: bool
    proc: subprocess.Popen
    started_at: float = field(default_factory=time.time)
    text: str = ""       # fin de la sortie (BUFFER_CHARS au plus)
    total: int = 0       # caractères reçus depuis le lancement
    read_upto: int = 0   # caractères déjà renvoyés à l'agent
    returncode: int | None = None
    finished_at: float | None = None
    stopped: bool = False  # arrêtée par stop_command, le bouton Arrêter ou la fin de tâche
    changed: threading.Condition = field(default_factory=threading.Condition)

    @property
    def running(self):
        return self.returncode is None

    def state(self):
        if self.running:
            return f"en cours depuis {int(time.time() - self.started_at)} s"
        if self.stopped:
            return "arrêtée"
        return f"terminée (code de sortie {self.returncode})"


_commands = {}
_counter = 0
_lock = threading.Lock()


def _reader(cmd, cleanup):
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    while True:
        chunk = cmd.proc.stdout.read1(4096) if hasattr(cmd.proc.stdout, "read1") else cmd.proc.stdout.read(4096)
        if not chunk:
            break
        with cmd.changed:
            piece = decoder.decode(chunk)
            cmd.text = (cmd.text + piece)[-BUFFER_CHARS:]
            cmd.total += len(piece)
            cmd.changed.notify_all()
    cmd.proc.stdout.close()  # sinon le tuyau reste ouvert jusqu'à la fin d'Aria
    code = cmd.proc.wait()
    with cmd.changed:
        cmd.returncode = code
        cmd.finished_at = time.time()
        cmd.changed.notify_all()
    cleanup()
    _forget_old(cmd.cid)


def _forget_old(cid):
    """Le registre ne garde que les MAX_FINISHED dernières commandes terminées de la conversation."""
    with _lock:
        finished = sorted((c for c in _commands.values() if c.cid == cid and not c.running),
                          key=lambda c: c.finished_at or 0)
        for cmd in finished[:-MAX_FINISHED] if len(finished) > MAX_FINISHED else []:
            _commands.pop(cmd.id, None)


def start(cid, task_id, command, argv, cwd, keep=False, cleanup=lambda: None):
    """Lance la commande ; `cleanup` est appelé quand elle se termine (suppression du script temporaire)."""
    global _counter
    with _lock:
        alive = [c for c in _commands.values() if c.cid == cid and c.running]
        if len(alive) >= MAX_PER_CONVERSATION:
            raise BackgroundError(f"Déjà {len(alive)} commandes en arrière-plan dans cette conversation "
                                  f"({', '.join(c.id for c in alive)}) : arrête-en une avec stop_command.")
        _counter += 1
        command_id = f"bg{_counter}"
    # Sans PYTHONUNBUFFERED, un script Python redirigé n'affiche rien avant de se terminer (sortie tamponnée)
    env = {**os.environ, **jobs.COMMAND_ENV, "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    jobs.attach(proc)
    cmd = Command(command_id, cid, task_id, command, keep, proc)
    _commands[command_id] = cmd
    threading.Thread(target=_reader, args=(cmd, cleanup), daemon=True).start()
    return cmd


def get(cid, command_id):
    cmd = _commands.get(command_id)
    if cmd is None or cmd.cid != cid:
        raise BackgroundError(f"Commande en arrière-plan inconnue : {command_id}.")
    return cmd


def read(cmd, wait_for=None, timeout=0.0):
    """Sortie apparue depuis la dernière lecture. Attend au plus `timeout` s que `wait_for` apparaisse
    (ou, sans wait_for, qu'il y ait du nouveau), et rend la main dès que la commande se termine."""
    timeout = max(0.0, min(float(timeout or 0), MAX_WAIT_S))
    deadline = time.time() + timeout
    with cmd.changed:
        while True:
            fresh = cmd.text[max(0, len(cmd.text) - (cmd.total - cmd.read_upto)):]
            found = wait_for is not None and wait_for.lower() in fresh.lower()
            done = found or not cmd.running or (wait_for is None and fresh)
            remaining = deadline - time.time()
            if done or remaining <= 0:
                break
            cmd.changed.wait(remaining)
        lost = cmd.total - cmd.read_upto - len(fresh)
        cmd.read_upto = cmd.total
    return fresh, found, lost


def wait_exit(cmd, timeout):
    """Attend au plus `timeout` s que la commande se termine ; renvoie True si elle tourne encore."""
    with cmd.changed:
        cmd.changed.wait_for(lambda: not cmd.running, timeout=timeout)
    return cmd.running


def stop(cmd):
    if cmd.running:
        cmd.stopped = True
        jobs.kill_tree(cmd.proc.pid)
        try:
            cmd.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
    with cmd.changed:
        cmd.changed.wait_for(lambda: not cmd.running, timeout=5)


def stop_task(cid, task_id):
    """Fin de tâche (ou Stop) : arrête les commandes de la tâche, sauf celles à garder."""
    for cmd in list(_commands.values()):
        if cmd.cid == cid and cmd.task_id == task_id and cmd.running and not cmd.keep:
            stop(cmd)


def stop_conversation(cid):
    for cmd in list(_commands.values()):
        if cmd.cid == cid and cmd.running:
            stop(cmd)


def stop_all():
    for cmd in list(_commands.values()):
        if cmd.running:
            stop(cmd)


def running(cid):
    return [{"id": c.id, "command": c.command, "keep": c.keep, "state": c.state()}
            for c in _commands.values() if c.cid == cid and c.running]


def peek(cmd, max_chars=PANEL_CHARS):
    """Fin de la sortie, pour le panneau latéral : ne touche pas à ce que l'agent n'a pas encore lu."""
    with cmd.changed:
        text = cmd.text[-max_chars:]
    # Fins de ligne Windows (PowerShell) et codes couleur : illisibles dans le panneau
    return ANSI.sub("", text).replace("\r\n", "\n").replace("\r", "")


def listing(cid, output=False):
    """Commandes de la conversation pour le panneau : celles qui tournent, puis les terminées, plus récentes d'abord."""
    cmds = sorted((c for c in _commands.values() if c.cid == cid),
                  key=lambda c: (not c.running, -(c.finished_at or c.started_at)))
    items = []
    for c in cmds:
        item = {"id": c.id, "command": c.command, "keep": c.keep, "running": c.running, "returncode": c.returncode,
                "state": c.state(), "started_at": c.started_at, "finished_at": c.finished_at}
        if output:
            item["output"] = peek(c)
        items.append(item)
    return items
