"""Base commune des tests : Aria tourne dans un dossier temporaire, jamais dans le tien.

À importer en premier dans chaque fichier de test (« from base import … ») : la variable ARIA_PILOTE_HOME
doit être posée avant le premier import d'aria_pilote. La corbeille Windows est remplacée par un dossier
temporaire, et aucun modèle n'est nécessaire (en-têtes GGUF dans donnees/, faux fichiers GGUF).
"""

import atexit
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

SRC = Path(__file__).resolve().parent.parent
HOME = Path(tempfile.mkdtemp(prefix="aria-tests-")).resolve()
os.environ["ARIA_PILOTE_HOME"] = str(HOME)
sys.path.insert(0, str(SRC))
# Port à part : les tests ne gênent jamais une Aria Pilote ouverte à côté
APP_PORT, LLAMA_PORT = 8799, 8798
(HOME / "config.json").write_text(json.dumps({"app_port": APP_PORT, "llama_port": LLAMA_PORT, "model_dirs": ["models"]}),
                                  encoding="utf-8")
atexit.register(shutil.rmtree, HOME, ignore_errors=True)

from aria_pilote import config, trash  # noqa: E402

# Garde-fou : on refuse de tourner si l'isolement n'est pas en place
if config.ROOT != HOME:
    sys.exit(f"Tests refusés : Aria pointerait sur {config.ROOT} au lieu du dossier temporaire {HOME}.")

BASE_URL = f"http://127.0.0.1:{APP_PORT}"
ORIGIN = {"Origin": BASE_URL}
DONNEES = Path(__file__).resolve().parent / "donnees"
GIB = 1024 ** 3

# --- Fausse corbeille ------------------------------------------------------------------
BIN = HOME / "corbeille"
BIN.mkdir()
sent_to_bin = []


def _fake_recycle_bin(paths):
    for path in paths:
        sent_to_bin.append(Path(path).name)
        shutil.move(str(path), BIN / f"{len(sent_to_bin)}-{Path(path).name}")


trash.to_recycle_bin = _fake_recycle_bin


# --- Utilitaires -------------------------------------------------------------------------

def workspace(name="projet"):
    """Dossier de travail neuf pour l'agent."""
    return Path(tempfile.mkdtemp(prefix=f"{name}-", dir=HOME))


def client():
    from fastapi.testclient import TestClient

    from aria_pilote import main
    return TestClient(main.app, base_url=BASE_URL)


def model_data(name):
    """En-tête GGUF et infos d'un vrai modèle (qwen, bonsai, smollm2), sans le fichier de plusieurs Go."""
    data = json.loads((DONNEES / f"{name}.json").read_text(encoding="utf-8"))
    # Projecteur vision : seule sa taille compte (lue par l'optimiseur via mmproj_size, voir test_optimiseur)
    model = SimpleNamespace(id=name, key=data["key"], name=data["name"], size_bytes=data["size_bytes"],
                            context_length=data["context_length"], engine_auto="llama.cpp",
                            mmproj=f"{name}-mmproj.gguf" if data["mmproj_bytes"] else None,
                            mmproj_size=data["mmproj_bytes"] or 0)
    return model, data["metadata"]


def fake_gguf(models_dir, rel="editeur/depot-GGUF/modele-Q4_K_M.gguf"):
    """Faux fichier GGUF de quelques octets : assez pour le scan des modèles."""
    path = Path(models_dir) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"pas un vrai gguf")
    return path


def new_conversation(root=None, enabled=True, **agent):
    from aria_pilote import conversations

    conv = conversations.create()
    if root is not None:
        conversations.update(conv["id"], lambda c: c.__setitem__(
            "agent", {"enabled": enabled, "workspace": str(root), **agent}))
    return conv["id"]


def events(raw_events):
    """Événements SSE (« data: {…} ») → dictionnaires."""
    return [json.loads(e[6:]) for e in raw_events]
