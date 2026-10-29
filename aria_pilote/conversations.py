"""Historique des conversations : un fichier JSON par conversation dans data/conversations/."""

import json
import re
import shutil
import threading
import uuid
from datetime import datetime, timedelta

from . import config, trash

DATA_DIR = config.ROOT / "data" / "conversations"
DEFAULT_TITLE = "Nouvelle conversation"
ID_RE = re.compile(r"^[0-9a-f]{12}$")

_lock = threading.Lock()


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _path(cid):
    if not ID_RE.match(cid):
        raise KeyError(cid)
    return DATA_DIR / f"{cid}.json"


def _make_title(content):
    text = " ".join(content.split())
    return text if len(text) <= 60 else text[:59] + "…"


def save(conv):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conv["updated_at"] = _now()
    path = _path(conv["id"])
    tmp = path.with_suffix(".json.tmp")
    with _lock:
        tmp.write_text(json.dumps(conv, indent=4, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    return conv


def create(preset_id=None):
    now = _now()
    conv = {
        "id": uuid.uuid4().hex[:12],
        "title": DEFAULT_TITLE,
        "created_at": now,
        "updated_at": now,
        "preset_id": preset_id,
        "messages": [],
    }
    return save(conv)


def get(cid):
    try:
        path = _path(cid)
    except KeyError:
        return None
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def ids():
    return {p.stem for p in DATA_DIR.glob("*.json")} if DATA_DIR.is_dir() else set()


def list_all():
    if not DATA_DIR.is_dir():
        return []
    items = []
    for path in DATA_DIR.glob("*.json"):
        conv = json.loads(path.read_text(encoding="utf-8"))
        items.append({
            "id": conv["id"],
            "title": conv["title"],
            "updated_at": conv["updated_at"],
            "message_count": len(conv["messages"]),
            "prive": bool(conv.get("prive")),
        })
    return sorted(items, key=lambda c: c["updated_at"], reverse=True)


def inactive_since(days, now=None):
    """Conversations sans activité depuis `days` jours (0 : aucune)."""
    if not days or not DATA_DIR.is_dir():
        return []
    limit = ((now or datetime.now()) - timedelta(days=days)).isoformat(timespec="seconds")
    return [c["id"] for c in list_all() if c["updated_at"] < limit]


def to_trash(cids):
    """Envoie ces conversations à la corbeille en un seul lot (avec leurs images) ; renvoie celles qui y sont parties."""
    paths = [_path(cid) for cid in cids if ID_RE.match(cid) and _path(cid).exists()]
    if paths:
        trash.to_recycle_bin(paths + [p.with_suffix("") for p in paths if p.with_suffix("").is_dir()])
    return [p.stem for p in paths]


def delete(cid):
    path = _path(cid)
    if path.exists():
        path.unlink()
    shutil.rmtree(path.with_suffix(""), ignore_errors=True)  # dossier des images jointes


def add_user_message(conv, content, skill=None, images=None, files=None):
    message = {"role": "user", "content": content, "created_at": _now()}
    if skill:
        message["skill"] = skill  # « /nom » tapé dans le chat : la compétence est ajoutée à l'envoi
    if images:
        message["images"] = images  # [{"file", "w", "h"}], rangées dans data/conversations/<id>/
    if files:
        message["files"] = files  # [{"name", "kind", "chars", "truncated", "text"}] : texte extrait (attachments.py)
    conv["messages"].append(message)
    if conv["title"] == DEFAULT_TITLE:
        fallback = f"📄 {files[0]['name']}" if files else "🖼 Image"
        conv["title"] = _make_title(f"/{skill} {content}" if skill else content or fallback)


def drop_trailing_assistant(conv):
    """Retire la dernière réponse, y compris les appels d'outils de l'agent."""
    while conv["messages"] and conv["messages"][-1]["role"] in ("assistant", "tool"):
        conv["messages"].pop()


def update(cid, change):
    """Relit la conversation, applique `change(conv)` et enregistre."""
    conv = get(cid)
    if conv is None:
        return None
    change(conv)
    return save(conv)


def append_message(cid, message):
    # On relit le fichier : la conversation a pu être renommée pendant la génération
    conv = get(cid)
    if conv is None:
        return None
    message.setdefault("created_at", _now())
    conv["messages"].append(message)
    return save(conv)
