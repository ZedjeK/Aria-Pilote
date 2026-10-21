"""Presets de chat (prompt système + échantillonnage), stockés dans data/presets.json."""

import json
import threading
import uuid

from . import config

PRESETS_PATH = config.ROOT / "data" / "presets.json"

# thinking : None laisse le modèle décider, True/False force la réflexion via le chat template
DEFAULT_PRESETS = [
    {
        "id": "default",
        "name": "Par défaut",
        "system_prompt": "Tu es une assistante utile. Réponds en français.",
        "temperature": 0.7,
        "top_p": 0.95,
        "top_k": 40,
        "min_p": 0.05,
        "presence_penalty": 0.0,
        "max_tokens": 4096,
        "thinking": None,
    },
    # Réglages conseillés par la fiche Hugging Face de Bonsai 2
    {
        "id": "bonsai-think",
        "name": "Bonsai réflexion",
        "system_prompt": "You are a helpful assistant",
        "temperature": 1.0,
        "top_p": 0.95,
        "top_k": 20,
        "min_p": 0.0,
        "presence_penalty": 1.0,
        "max_tokens": 16384,
        "thinking": True,
    },
    {
        "id": "bonsai-fast",
        "name": "Bonsai rapide",
        "system_prompt": "You are a helpful assistant",
        "temperature": 0.7,
        "top_p": 0.8,
        "top_k": 20,
        "min_p": 0.0,
        "presence_penalty": 1.5,
        "max_tokens": 8192,
        "thinking": False,
    },
]

_lock = threading.Lock()


def _write(presets):
    PRESETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PRESETS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(presets, indent=4, ensure_ascii=False), encoding="utf-8")
    tmp.replace(PRESETS_PATH)


def list_all():
    with _lock:
        if not PRESETS_PATH.exists():
            _write(DEFAULT_PRESETS)
        return json.loads(PRESETS_PATH.read_text(encoding="utf-8"))


def get(pid):
    return next((p for p in list_all() if p["id"] == pid), None)


def resolve(pid):
    """Preset demandé, sinon le premier disponible."""
    return get(pid) or list_all()[0]


def create(data):
    preset = {"id": uuid.uuid4().hex[:8], **data}
    presets = list_all()
    presets.append(preset)
    with _lock:
        _write(presets)
    return preset


def update(pid, data):
    presets = list_all()
    for p in presets:
        if p["id"] == pid:
            p.update(data)
            with _lock:
                _write(presets)
            return p
    return None


def delete(pid):
    presets = list_all()
    remaining = [p for p in presets if p["id"] != pid]
    if not remaining:
        raise ValueError("Impossible de supprimer le dernier preset.")
    with _lock:
        _write(remaining)
