"""Moteurs d'inférence : llama.cpp officiel et fork PrismML (requis pour Bonsai PQ2_0 / PTQ1_0)."""

import json

from . import config

ENGINES = {
    "llama.cpp": {"label": "llama.cpp officiel", "dir": "llama.cpp"},
    "prism": {"label": "llama.cpp PrismML (Bonsai)", "dir": "prism"},
}

# Formats que seul le fork PrismML sait lire (l'officiel les rejette comme types inconnus)
PRISM_QUANTS = {"PQ2_0", "PTQ1_0"}


def auto_engine(quant):
    return "prism" if quant in PRISM_QUANTS else "llama.cpp"


def engine_dir(name):
    return config.path(config.get()["engines_dir"]) / ENGINES[name]["dir"]


def find_server(name):
    base = engine_dir(name)
    if not base.is_dir():
        return None
    return next(base.rglob("llama-server.exe"), None)


def info(name):
    exe = find_server(name)
    release = None
    manifest = engine_dir(name) / "engine.json"
    if manifest.exists():
        release = json.loads(manifest.read_text(encoding="utf-8"))
    return {
        "name": name,
        "label": ENGINES[name]["label"],
        "installed": exe is not None,
        "path": str(exe) if exe else None,
        "release": release,
    }


def all_info():
    return [info(name) for name in ENGINES]
