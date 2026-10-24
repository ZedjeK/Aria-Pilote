"""Fenêtre « Choisir un dossier » de Windows, ouverte pour la page : un navigateur ne donne jamais le chemin complet
d'un dossier, le serveur d'Aria (qui tourne sur ton PC) ouvre donc la boîte de dialogue lui-même.

Elle tourne dans un petit processus à part : le serveur ne fige jamais, et une fenêtre oubliée est fermée au bout
de MAX_WAIT_S. Une seule à la fois.
"""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

from . import jobs

MAX_WAIT_S = 600
UNAVAILABLE = 3  # code de sortie du processus quand tkinter manque

# Le processus enfant : la boîte de dialogue au premier plan, puis le chemin choisi en JSON sur sa sortie
SCRIPT = r"""
import json, sys
try:
    import tkinter
    from tkinter import filedialog
except ImportError:
    sys.exit(3)
initial, title = sys.argv[1], sys.argv[2]
root = tkinter.Tk()
root.withdraw()
root.attributes("-topmost", True)  # devant le navigateur
root.update()
path = filedialog.askdirectory(parent=root, initialdir=initial or None, title=title, mustexist=True)
root.destroy()
print(json.dumps({"path": path or None}))
"""

_lock = asyncio.Lock()


class PickerError(Exception):
    pass


class PickerBusy(PickerError):
    pass


class PickerUnavailable(PickerError):
    pass


def _initial(initial):
    """Point de départ : le dossier donné s'il existe encore, sinon son plus proche parent existant."""
    if not initial:
        return ""
    path = Path(initial).expanduser()
    while not path.is_dir() and path.parent != path:
        path = path.parent
    return str(path) if path.is_dir() else ""


def _run(initial, title):
    if sys.platform != "win32":
        raise PickerUnavailable("La fenêtre de choix n'existe que sous Windows : saisis le chemin à la main.")
    proc = subprocess.Popen([sys.executable, "-c", SCRIPT, _initial(initial), title],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    jobs.attach(proc)  # fermée avec Aria si on la quitte pendant que la fenêtre est ouverte
    try:
        out, _ = proc.communicate(timeout=MAX_WAIT_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return None
    if proc.returncode == UNAVAILABLE:
        raise PickerUnavailable("La fenêtre de choix n'est pas disponible (tkinter absent) : saisis le chemin à la main.")
    if proc.returncode != 0:
        raise PickerError("La fenêtre de choix s'est fermée sur une erreur.")
    try:
        path = json.loads(out.decode("utf-8", errors="replace").strip() or "{}").get("path")
    except ValueError:
        raise PickerError("Réponse illisible de la fenêtre de choix.")
    return str(Path(path)) if path else None  # chemin Windows (antislashs), ou None si tu as annulé


async def pick(initial="", title="Choisir un dossier"):
    """Chemin du dossier choisi, ou None si tu as annulé (ou laissé la fenêtre trop longtemps)."""
    if _lock.locked():
        raise PickerBusy("Une fenêtre de choix est déjà ouverte : regarde derrière le navigateur ou dans la barre des tâches.")
    async with _lock:
        return await asyncio.to_thread(_run, initial, title)
