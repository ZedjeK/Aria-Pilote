"""Configuration d'Aria Pilote, stockée dans config.json à la racine du projet."""

import copy
import json
import os
import threading
from pathlib import Path

# Dossier de l'appli (config, data, modèles…). ARIA_PILOTE_HOME le remplace : la suite de tests fait ainsi
# tourner Aria dans un dossier temporaire, sans jamais toucher à tes données.
ROOT = Path(os.environ.get("ARIA_PILOTE_HOME") or Path(__file__).resolve().parent.parent).resolve()
CONFIG_PATH = ROOT / "config.json"

LOAD_FIELDS = ("engine", "ctx_size", "gpu_layers", "threads", "flash_attn", "use_mmproj", "cache_type")
# Type du cache de contexte (KV) : f16 par défaut ; q8_0 le divise par deux presque sans perte, q4_0 par quatre
CACHE_TYPES = ("f16", "q8_0", "q4_0")

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
    # Modèle proposé par le sélecteur du haut (identifiant, "" : aucun), chargé au démarrage si autoload
    "default_model": "",
    "autoload": False,
    "searxng_url": "http://127.0.0.1:8888",
    # Vérification de la syntaxe des fichiers que l'agent écrit (Python, JSON, YAML, PowerShell…)
    "auto_check": True,
    # Sauvegardes de l'agent (pour « Annuler ») gardées ce nombre de jours, puis envoyées à la corbeille (0 : toujours)
    "backup_retention_days": 30,
    # Règles « toujours autoriser » des commandes de l'agent, par dossier de projet (voir permissions.py)
    "command_rules": {},
    # Réglages de départ d'une nouvelle conversation. Le dossier proposé ne s'attache jamais tout seul : c'est ton
    # geste qui ouvre les outils de fichiers. Jamais d'« écriture autorisée d'office » non plus.
    "new_conversation": {"workspace": "", "auto_web": False},
    # Plan exigé avant toute modification, à chaque nouvelle tâche dans un dossier (verrou côté serveur)
    "always_plan": False,
    # Contrôle du PC (chantier 4) : coupé tant que tu ne l'actives pas ici, puis conversation par conversation
    "screen_control": False,
    # Preset des nouvelles conversations (et de celles dont le preset a été supprimé)
    "default_preset": "default",
    # Persona des nouvelles conversations (et de celles dont le persona a été supprimé)
    "default_persona": "aria",
    # Appels d'outils au plus par tâche de l'agent
    "max_tool_calls": 25,
    # Recherche web : coupée, l'agent ne voit plus web_search ni web_fetch (ni les sous-agents qui n'ont que ça)
    "web": {"enabled": True, "results": 8, "language": "auto", "safesearch": 0},
    # SearXNG arrêté après ce nombre de minutes sans recherche (0 : tourne jusqu'à la fermeture d'Aria)
    "searxng_idle_minutes": 0,
    # Conversations inactives depuis ce nombre de jours envoyées à la corbeille au démarrage (0 : jamais)
    "conversation_retention_days": 0,
    # Recherche d'une nouvelle version sur GitHub au démarrage (une requête anonyme, au plus une fois par jour)
    "update_check": True,
    # Notifications du navigateur (centre de notifications de Windows) : coupées tant que tu ne les actives pas
    "notifications": {"enabled": False, "only_background": True, "task": True, "waiting": True,
                      "model": True, "download": True},
    # Réglages de chargement par défaut ; threads = 0 laisse llama.cpp choisir.
    "load_defaults": {
        "engine": "auto",
        "ctx_size": 8192,
        "gpu_layers": 99,
        "threads": 0,
        "flash_attn": "auto",
        "use_mmproj": True,
        "cache_type": "f16",
    },
    # Réglages mémorisés par modèle, indexés par chemin du fichier GGUF.
    "model_overrides": {},
}

WEB_LANGUAGES = ("auto", "fr", "en", "de", "es", "it", "ja")

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


def web_enabled():
    return bool(get()["web"].get("enabled", True))


def update(patch):
    global _data
    get()
    with _lock:
        _data = _merge(_data, patch)
        _save_locked()
        return copy.deepcopy(_data)


# Les réglages par modèle sont indexés par la clé du modèle : son chemin relatif au dossier de modèles
# (voir models.py), qui ne change pas quand le dossier de l'appli change de nom à chaque version.

def model_settings(key):
    cfg = get()
    settings = dict(cfg["load_defaults"])
    settings.update(cfg["model_overrides"].get(key, {}))
    return settings


def forget_model(key):
    """Oublie les réglages mémorisés d'un modèle supprimé."""
    global _data
    get()
    with _lock:
        if _data["model_overrides"].pop(key, None) is not None:
            _save_locked()


def save_model_settings(key, settings):
    clean = {k: v for k, v in settings.items() if k in LOAD_FIELDS}
    update({"model_overrides": {key: clean}})


def migrate_model_keys(keys):
    """Anciennes clés (chemin complet du GGUF, avant la 0.9.1) → clé relative du modèle qui y correspond."""
    global _data
    get()
    with _lock:
        overrides = _data["model_overrides"]
        moved = False
        for old in list(overrides):
            if not Path(old).is_absolute():
                continue
            tail = old.replace("\\", "/").lower()
            match = next((k for k in keys if tail.endswith("/" + k.split(":", 1)[-1].lower())), None)
            if match and match not in overrides:
                overrides[match] = overrides.pop(old)
                moved = True
        if moved:
            _save_locked()
