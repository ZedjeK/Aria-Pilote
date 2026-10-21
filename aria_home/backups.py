"""Sauvegarde d'un fichier avant que l'agent le modifie, pour pouvoir annuler."""

import json
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from . import config

BACKUP_DIR = config.ROOT / "data" / "backups"
ID_RE = re.compile(r"^[0-9a-f]{12}$")


class BackupError(Exception):
    pass


def _dir(cid, backup_id):
    if not ID_RE.match(cid) or not ID_RE.match(backup_id):
        raise BackupError("Identifiant de sauvegarde invalide.")
    return BACKUP_DIR / cid / backup_id


def save(cid, target):
    backup_id = uuid.uuid4().hex[:12]
    folder = _dir(cid, backup_id)
    folder.mkdir(parents=True)
    existed = target.exists()
    if existed:
        shutil.copy2(target, folder / "original")
    meta = {"path": str(target), "existed": existed, "created_at": datetime.now().isoformat(timespec="seconds")}
    (folder / "meta.json").write_text(json.dumps(meta, indent=4, ensure_ascii=False), encoding="utf-8")
    return backup_id


def restore(cid, backup_id):
    """Remet le fichier dans l'état d'avant la modification (le supprime s'il n'existait pas)."""
    folder = _dir(cid, backup_id)
    if not (folder / "meta.json").exists():
        raise BackupError("Sauvegarde introuvable.")
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    target = Path(meta["path"])
    if meta["existed"]:
        shutil.copy2(folder / "original", target)
    elif target.exists():
        target.unlink()
    return meta
