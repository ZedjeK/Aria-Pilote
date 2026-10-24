"""Presets de chat (réflexion et échantillonnage), stockés dans data/presets.json.

Le prompt système n'en fait plus partie : qui parle, c'est le persona ; à qui, c'est la fiche « Toi »."""

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
        "temperature": 0.7,
        "top_p": 0.95,
        "top_k": 40,
        "min_p": 0.05,
        "presence_penalty": 0.0,
        "max_tokens": 4096,
        "thinking": None,
    },
    # Presets universels, valables pour tout modèle qui sait raisonner (valeurs d'origine : fiche de Bonsai 2,
    # proches de celles conseillées pour la famille Qwen)
    {
        "id": "reflexion",
        "name": "Réflexion",
        "temperature": 1.0,
        "top_p": 0.95,
        "top_k": 20,
        "min_p": 0.0,
        "presence_penalty": 1.0,
        "max_tokens": 16384,
        "thinking": True,
    },
    {
        "id": "rapide",
        "name": "Rapide",
        "temperature": 0.7,
        "top_p": 0.8,
        "top_k": 20,
        "min_p": 0.0,
        "presence_penalty": 1.5,
        "max_tokens": 8192,
        "thinking": False,
    },
]

# Prompts système livrés autrefois avec les presets : supprimés sans rien dire (la langue vient de la fiche « Toi »)
SHIPPED_PROMPTS = {"Tu es une assistante utile. Réponds en français.", "You are a helpful assistant", ""}

# Anciens presets livrés au nom d'un modèle : renommés s'ils n'ont pas été touchés (leur identifiant ne change pas,
# pour que les conversations qui les utilisent les retrouvent)
LEGACY_NAMES = {"bonsai-think": ("Bonsai réflexion", "Réflexion"), "bonsai-fast": ("Bonsai rapide", "Rapide")}

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
        presets = json.loads(PRESETS_PATH.read_text(encoding="utf-8"))
        changed = False
        for preset in presets:
            old, new = LEGACY_NAMES.get(preset.get("id"), (None, None))
            if old and preset.get("name") == old:
                preset["name"] = new
                changed = True
            if "system_prompt" in preset:
                _migrate_prompt(preset)
                changed = True
        if changed:
            _write(presets)
        return presets


def _migrate_prompt(preset):
    """Un prompt système écrit à la main devient un persona « <preset> (prompt importé) » : rien ne se perd."""
    text = (preset.pop("system_prompt", "") or "").strip()
    if text in SHIPPED_PROMPTS:
        return
    from . import persona  # import tardif : persona ne dépend pas des presets

    try:
        persona.create({"name": f"{preset.get('name', 'Preset')} (prompt importé)"[:40],
                        "presentation": text[:persona.MAX_PRESENTATION], "gender": "neutre"})
    except persona.PersonaError as exc:
        print(f"Prompt du preset « {preset.get('name')} » non importé : {exc}")


def get(pid):
    return next((p for p in list_all() if p["id"] == pid), None)


def resolve(pid):
    """Preset demandé, sinon le preset par défaut des réglages (preset supprimé…), sinon le premier disponible."""
    from . import config  # config lit ce module indirectement : import tardif

    return get(pid) or get(config.get().get("default_preset")) or list_all()[0]


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
