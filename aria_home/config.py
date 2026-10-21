"""Configuration d'Aria Home, stockée dans config.json à la racine du projet."""

import copy
import json
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"

LOAD_FIELDS = ("engine", "ctx_size", "gpu_layers", "threads", "flash_attn", "use_mmproj")

# Les chemins relatifs sont résolus par rapport au dossier de l'appli (voir path()),
# pour que le dossier reste valable une fois copié ailleurs.
DEFAULTS = {
    "app_port": 8700,
    "llama_port": 8701,
    # Le premier dossier reçoit les téléchargements ; celui de LM Studio est seulement
    # scanné s'il existe sur la machine
    "model_dirs": [
        "models",
        "~/.lmstudio/models",
    ],
    "engines_dir": "engines",
    "searxng_url": "http://127.0.0.1:8888",
    # Réglages de chargement par défaut ; threads = 0 laisse llama.cpp choisir.
    "load_defaults": {
        "engine": "auto",
        "ctx_size": 8192,
        "gpu_layers": 99,
        "threads": 0,
        "flash_attn": "auto",
        "use_mmproj": True,
    },
    # Réglages mémorisés par modèle, indexés par chemin du fichier GGUF.
    "model_overrides": {},
}

_lock = threading.Lock()
_data = None


def path(value):
    """Chemin absolu : « ~ » est développé, un chemin relatif part du dossier de l'appli."""
    p = Path(value).expanduser()
    return p if p.is_absolute() else ROOT / p


def _merge(base, patch):
    out = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _save_locked():
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(_data, indent=4, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CONFIG_PATH)


def get():
    global _data
    with _lock:
        if _data is None:
            stored = {}
            if CONFIG_PATH.exists():
                stored = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            _data = _merge(DEFAULTS, stored)
            _save_locked()
        return copy.deepcopy(_data)


def update(patch):
    global _data
    get()
    with _lock:
        _data = _merge(_data, patch)
        _save_locked()
        return copy.deepcopy(_data)


def model_settings(path):
    cfg = get()
    settings = dict(cfg["load_defaults"])
    settings.update(cfg["model_overrides"].get(str(path), {}))
    return settings


def forget_model(path):
    """Oublie les réglages mémorisés d'un modèle supprimé."""
    global _data
    get()
    with _lock:
        if _data["model_overrides"].pop(str(path), None) is not None:
            _save_locked()


def save_model_settings(path, settings):
    clean = {k: v for k, v in settings.items() if k in LOAD_FIELDS}
    update({"model_overrides": {str(path): clean}})
