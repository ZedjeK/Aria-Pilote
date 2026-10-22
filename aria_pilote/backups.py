"""Sauvegarde d'un fichier avant que l'agent le modifie, pour pouvoir annuler.

Chaque sauvegarde est étiquetée avec la tâche en cours (une demande de l'utilisateur, reprises comprises) :
on peut ainsi annuler une modification seule, ou toute une tâche d'un coup.
"""

import hashlib
import json
import re
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path

from . import config, trash

BACKUP_DIR = config.ROOT / "data" / "backups"
ID_RE = re.compile(r"^[0-9a-f]{12}$")

# Tâche en cours par conversation : conversation_id -> task_id (posé par l'agent le temps de son travail)
_running = {}


class BackupError(Exception):
    pass


def new_task_id():
    return uuid.uuid4().hex[:12]


def set_running(cid, task_id):
    if task_id is None:
        _running.pop(cid, None)
    else:
        _running[cid] = task_id


def is_running(cid):
    return cid in _running


def running_task(cid):
    return _running.get(cid)


def _dir(cid, backup_id):
    if not ID_RE.match(cid) or not ID_RE.match(backup_id):
        raise BackupError("Identifiant de sauvegarde invalide.")
    return BACKUP_DIR / cid / backup_id


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if Path(path).is_file() else None


def _read_meta(folder):
    return json.loads((folder / "meta.json").read_text(encoding="utf-8"))


def _write_meta(folder, meta):
    (folder / "meta.json").write_text(json.dumps(meta, indent=4, ensure_ascii=False), encoding="utf-8")


def save(cid, target):
    backup_id = uuid.uuid4().hex[:12]
    folder = _dir(cid, backup_id)
    folder.mkdir(parents=True)
    existed = target.exists()
    if existed:
        shutil.copy2(target, folder / "original")
    _write_meta(folder, {"path": str(target), "existed": existed, "task_id": _running.get(cid),
                         "seq": time.time_ns(), "created_at": datetime.now().isoformat(timespec="seconds")})
    return backup_id


def record_after(cid, backup_id, target):
    """Empreinte du fichier juste après l'écriture de l'agent : sert à repérer une retouche ultérieure."""
    folder = _dir(cid, backup_id)
    meta = _read_meta(folder)
    meta["after"] = _digest(target)
    _write_meta(folder, meta)


def restore(cid, backup_id):
    """Remet le fichier dans l'état d'avant la modification (le supprime s'il n'existait pas)."""
    folder = _dir(cid, backup_id)
    if not (folder / "meta.json").exists():
        raise BackupError("Sauvegarde introuvable.")
    meta = _read_meta(folder)
    target = Path(meta["path"])
    if meta["existed"]:
        shutil.copy2(folder / "original", target)
    elif target.exists():
        target.unlink()
    meta["undone"] = True
    _write_meta(folder, meta)
    return meta


# --- Annulation d'une tâche entière --------------------------------------------------

def _task_backups(cid, task_id):
    """Sauvegardes encore actives de la tâche, de la plus ancienne à la plus récente, avec leur dossier."""
    base = BACKUP_DIR / cid
    if not ID_RE.match(cid) or not ID_RE.match(task_id) or not base.is_dir():
        return []
    found = []
    for folder in base.iterdir():
        try:
            meta = _read_meta(folder)
        except (OSError, ValueError):
            continue
        if meta.get("task_id") == task_id and not meta.get("undone"):
            found.append((folder, meta))
    return sorted(found, key=lambda item: item[1].get("seq", 0))


def task_plan(cid, task_id):
    """Ce que l'annulation ferait, fichier par fichier : restaurer, supprimer, ou conflit (retouché depuis)."""
    by_path = {}
    for folder, meta in _task_backups(cid, task_id):
        by_path.setdefault(meta["path"], []).append((folder, meta))
    plan = []
    for path, items in by_path.items():
        first_folder, first = items[0]  # état d'avant la tâche
        last = items[-1][1]            # état laissé par l'agent
        current = _digest(path)
        if last.get("after") is not None:
            conflict = current != last["after"]
        else:  # écriture qui a échoué : le fichier ne doit pas avoir bougé depuis la sauvegarde
            conflict = current != (_digest(first_folder / "original") if first["existed"] else None)
        plan.append({"path": path, "action": "restaurer" if first["existed"] else "supprimer",
                     "conflict": conflict, "backups": [f.name for f, _ in items]})
    return plan


def undo_task(cid, task_id, force=False):
    """Remet chaque fichier de la tâche dans son état d'avant ; la version actuelle part à la corbeille.

    Les fichiers retouchés depuis la tâche (conflits) sont laissés tels quels, sauf avec force=True.
    """
    result = {"restored": [], "deleted": [], "skipped": [], "errors": []}
    for entry in task_plan(cid, task_id):
        if entry["conflict"] and not force:
            result["skipped"].append(entry["path"])
            continue
        target = Path(entry["path"])
        first = _dir(cid, entry["backups"][0])
        try:
            if target.exists():
                trash.to_recycle_bin([target])
            if entry["action"] == "restaurer":
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(first / "original", target)
        except OSError as exc:
            result["errors"].append(f"{entry['path']} : {exc}")
            continue
        for backup_id in entry["backups"]:
            folder = _dir(cid, backup_id)
            meta = _read_meta(folder)
            meta["undone"] = True
            _write_meta(folder, meta)
        result["restored" if entry["action"] == "restaurer" else "deleted"].append(entry["path"])
    return result
