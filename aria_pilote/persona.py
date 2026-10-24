"""Personas (qui parle) et fiche « Toi » (à qui elle parle), et leur mise en texte pour le prompt.

Un persona n'est pas un texte libre : des traits prédéfinis (catalogue persona_traits.json, identifiants fixes),
chacun avec une intensité par registre (naturel, sobre, professionnel). Le registre en vigueur dépend de la
compétence chargée ; sans compétence, c'est celui de la discussion. Le modèle ne lit que la colonne en vigueur.

Stockage : data/personas/<id>.json et data/profil.json, dans les données de l'appli (jamais dans le dépôt).
"""

import json
import re
import threading
import uuid
from pathlib import Path

from . import config

CATALOGUE = json.loads((Path(__file__).parent / "persona_traits.json").read_text(encoding="utf-8"))
TRAITS = {t["id"]: t for t in CATALOGUE["traits"]}
THEMES = {t["id"]: t["label"] for t in CATALOGUE["themes"]}
REGISTERS = {r["id"]: r["label"] for r in CATALOGUE["registers"]}
INTENSITIES = {1: "légère", 2: "marquée", 3: "très marquée"}
GENDERS = {"feminin": "au féminin", "masculin": "au masculin", "neutre": "sans marque de genre (tournures neutres)"}
FORMAT = 1  # numéro de format des fichiers exportés

DATA_DIR = config.ROOT / "data" / "personas"
PROFILE_PATH = config.ROOT / "data" / "profil.json"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
DISCUSSION = "discussion"
DEFAULT_REGISTER = "sobre"  # une compétence ajoutée plus tard démarre en sobre

MAX_RULES, MAX_RULE_CHARS, MAX_PRESENTATION = 20, 300, 2000
LANGUAGES = {"fr": "français", "en": "anglais", "de": "allemand", "es": "espagnol", "it": "italien"}
PROFILE_GENDERS = {"masculin": "accord au masculin", "feminin": "accord au féminin", "neutre": "accord neutre",
                   "": "accord non précisé : tournures neutres"}

# Le persona livré : neutre, pour tout le monde. Le tien se crée dans l'appli et reste dans tes données.
DEFAULT_PERSONA = {
    "id": "aria",
    "name": "Aria",
    "avatar": "🤖",
    "gender": "feminin",
    "presentation": "L'assistante d'Aria Pilote : claire, fiable et à l'écoute.",
    "traits": {
        "chaleur": {"naturel": 2, "sobre": 1, "professionnel": 0},
        "franchise": {"naturel": 2, "sobre": 2, "professionnel": 2},
        "patience": {"naturel": 2, "sobre": 1, "professionnel": 0},
        "reponse-d-abord": {"naturel": 2, "sobre": 2, "professionnel": 3},
        "concision": {"naturel": 2, "sobre": 1, "professionnel": 0},
    },
    "themes": [],
    "rules": ["Ne jamais inventer un fait : dire quand on ne sait pas, puis vérifier ou demander."],
    "intimate": False,
    "registers": {DISCUSSION: "naturel"},
}

EMPTY_PROFILE = {"name": "", "gender": "", "address": "tu", "language": "fr", "job": "", "likes": "", "about": "",
                 "in_folder": True}

_lock = threading.Lock()


class PersonaError(ValueError):
    pass


# --- Validation ------------------------------------------------------------------------------

def _text(value, limit, field):
    text = str(value or "").strip()
    if len(text) > limit:
        raise PersonaError(f"{field} : {limit} caractères au plus.")
    return text


def clean(data, warnings=None):
    """Persona validé et normalisé. Les traits ou thèmes inconnus (fichier d'une version plus récente…) sont
    écartés, avec un avertissement dans `warnings` si la liste est fournie, plutôt que de tout refuser."""
    warnings = warnings if warnings is not None else []
    name = _text(data.get("name"), 40, "Nom")
    if not name:
        raise PersonaError("Donne-lui un nom.")
    gender = data.get("gender") or "feminin"
    if gender not in GENDERS:
        raise PersonaError(f"Genre inconnu : {gender}")
    traits = {}
    for tid, levels in (data.get("traits") or {}).items():
        if tid not in TRAITS:
            warnings.append(f"Trait inconnu ignoré : {tid}")
            continue
        traits[tid] = {}
        for register in REGISTERS:
            try:
                level = int((levels or {}).get(register, 0))
            except (TypeError, ValueError):
                level = 0
            traits[tid][register] = min(3, max(0, level))
    themes = []
    for theme in data.get("themes") or []:
        if theme in THEMES:
            themes.append(theme) if theme not in themes else None
        else:
            warnings.append(f"Thème inconnu ignoré : {theme}")
    rules = [_text(r, MAX_RULE_CHARS, "Règle d'or") for r in (data.get("rules") or [])]
    rules = [r for r in rules if r][:MAX_RULES]
    registers = {}
    for context, register in (data.get("registers") or {}).items():
        if register in REGISTERS and re.match(r"^[a-z0-9][a-z0-9-]{0,40}$", str(context)):
            registers[context] = register
    registers.setdefault(DISCUSSION, "naturel")
    return {
        "name": name,
        "avatar": _text(data.get("avatar"), 8, "Avatar"),
        "gender": gender,
        "presentation": _text(data.get("presentation"), MAX_PRESENTATION, "Présentation"),
        "traits": traits,
        "themes": themes,
        "rules": rules,
        "intimate": bool(data.get("intimate")),
        "registers": registers,
    }


def clean_profile(data):
    gender = data.get("gender") or ""
    if gender not in PROFILE_GENDERS:
        raise PersonaError(f"Accord inconnu : {gender}")
    address = data.get("address") or "tu"
    if address not in ("tu", "vous"):
        raise PersonaError("Adresse : tutoiement ou vouvoiement.")
    language = data.get("language") or "fr"
    if language not in LANGUAGES:
        raise PersonaError(f"Langue inconnue : {language}")
    return {
        "name": _text(data.get("name"), 40, "Prénom"),
        "gender": gender,
        "address": address,
        "language": language,
        "job": _text(data.get("job"), 200, "Métier"),
        "likes": _text(data.get("likes"), 300, "Ce que tu apprécies"),
        "about": _text(data.get("about"), 3000, "À propos de toi"),
        "in_folder": bool(data.get("in_folder", True)),
    }


# --- Stockage --------------------------------------------------------------------------------

def _path(pid):
    if not ID_RE.match(pid or ""):
        raise PersonaError("Identifiant de persona invalide.")
    return DATA_DIR / f"{pid}.json"


def _write(pid, persona):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = _path(pid)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"id": pid, **persona}, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def list_all():
    """Tous les personas, triés par nom ; le persona livré est créé au premier appel."""
    with _lock:
        if not DATA_DIR.is_dir() or not any(DATA_DIR.glob("*.json")):
            persona = dict(DEFAULT_PERSONA)
            _write(persona.pop("id"), persona)
        items = []
        for path in DATA_DIR.glob("*.json"):
            try:
                items.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue  # fichier abîmé : ignoré plutôt que de tout bloquer
    return sorted(items, key=lambda p: p.get("name", "").lower())


def get(pid):
    if not pid or not ID_RE.match(pid):
        return None
    return next((p for p in list_all() if p["id"] == pid), None)


def _new_id(name):
    base = re.sub(r"[^a-z0-9]+", "-", name.lower().encode("ascii", "ignore").decode()).strip("-")[:30] or "persona"
    pid = base
    while _path(pid).exists():
        pid = f"{base}-{uuid.uuid4().hex[:4]}"
    return pid


def create(data, warnings=None):
    list_all()  # le persona livré existe toujours, même si la première action est une création
    persona = clean(data, warnings)
    pid = _new_id(persona["name"])
    with _lock:
        _write(pid, persona)
    return {"id": pid, **persona}


def update(pid, data):
    if get(pid) is None:
        return None
    persona = clean(data)
    with _lock:
        _write(pid, persona)
    return {"id": pid, **persona}


def delete(pid):
    if len(list_all()) <= 1:
        raise PersonaError("Impossible de supprimer le dernier persona.")
    path = _path(pid)
    if not path.exists():
        raise PersonaError("Persona introuvable.")
    with _lock:
        path.unlink()
    if config.get().get("default_persona") == pid:
        config.update({"default_persona": list_all()[0]["id"]})


def export(pid):
    persona = get(pid)
    if persona is None:
        raise PersonaError("Persona introuvable.")
    data = {k: v for k, v in persona.items() if k != "id"}
    return {"format": FORMAT, "type": "aria-pilote-persona", **data}


def import_(data):
    """Persona importé d'un fichier exporté ; renvoie (persona, avertissements)."""
    if data.get("type") != "aria-pilote-persona":
        raise PersonaError("Ce fichier n'est pas un persona d'Aria Pilote.")
    if int(data.get("format") or 0) > FORMAT:
        raise PersonaError("Ce persona vient d'une version plus récente d'Aria Pilote : mets-la à jour d'abord.")
    warnings = []
    return create(data, warnings), warnings


def default_id():
    pid = config.get().get("default_persona") or DEFAULT_PERSONA["id"]
    return pid if get(pid) else (list_all() or [{}])[0].get("id")


def for_conversation(conv):
    """Persona de la conversation : le sien, sinon le persona par défaut ; None pour « Sans persona »."""
    pid = (conv or {}).get("persona_id")
    if pid == "none":
        return None
    return get(pid) or get(default_id())


def get_profile():
    try:
        stored = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = {}
    return {**EMPTY_PROFILE, **stored}


def save_profile(data):
    profile = clean_profile(data)
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROFILE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(PROFILE_PATH)
    return profile


# --- Mise en texte pour le modèle -------------------------------------------------------------

def register_for(persona, skill=None):
    """Registre en vigueur : celui de la compétence chargée, sinon celui de la discussion."""
    registers = (persona or {}).get("registers") or {}
    if skill:
        return registers.get(skill, DEFAULT_REGISTER)
    return registers.get(DISCUSSION, "naturel")


def personality(persona, register):
    """Les traits de la colonne en vigueur (un trait à 0 n'apparaît pas), et les goûts s'ils sont présents."""
    lines = []
    for tid, levels in (persona.get("traits") or {}).items():
        level = levels.get(register, 0)
        trait = TRAITS.get(tid)
        if not trait or not level or tid == "gouts":
            continue
        lines.append(f"- {trait['label']} (intensité {INTENSITIES[level]}) : {trait['definition']}.")
    tastes = (persona.get("traits") or {}).get("gouts", {}).get(register, 0)
    themes = [THEMES[t] for t in persona.get("themes") or [] if t in THEMES]
    if tastes and themes:
        lines.append(f"- Tes goûts, à évoquer de façon {INTENSITIES[tastes]} : {', '.join(themes)}.")
    return lines


def render(persona, register):
    """Bloc « qui tu es » du prompt, pour le registre donné."""
    avatar = f" {persona['avatar']}" if persona.get("avatar") else ""
    parts = [f"Qui tu es : tu es {persona['name']}{avatar}. Tu parles de toi {GENDERS[persona.get('gender', 'feminin')]}."]
    if persona.get("presentation"):
        parts.append(f"Présentation : {persona['presentation']}")
    lines = personality(persona, register)
    if lines:
        parts.append(f"Ta personnalité (registre {REGISTERS[register].lower()}) :\n" + "\n".join(lines))
    if persona.get("rules"):
        rules = "\n".join(f"{n}. {r}" for n, r in enumerate(persona["rules"], 1))
        parts.append("Tes règles d'or (elles passent avant tout, sauf les règles de sécurité d'Aria Pilote) :\n" + rules)
    parts.append("Contenu intime : autorisé ; l'explicite est permis, sans excuse ni avertissement."
                 if persona.get("intimate") else "Contenu intime : non autorisé ; reste correct, sans contenu explicite.")
    return "\n".join(parts)


def register_note(persona, skill):
    """Ajouté au contenu d'une compétence quand elle est chargée : le registre de ce mode."""
    if not persona:
        return ""
    register = register_for(persona, skill)
    lines = personality(persona, register)
    body = "\n".join(lines) if lines else "- aucun trait de personnalité dans ce registre : reste neutre."
    return (f"\n\nPour cette compétence, adopte le registre {REGISTERS[register].lower()} de ta personnalité :\n{body}\n"
            "À la demande suivante sans compétence, reviens à ton registre de discussion.")


def render_profile(profile):
    """Bloc « l'utilisateur » du prompt, ou "" si la fiche est vide."""
    p = {**EMPTY_PROFILE, **(profile or {})}
    if not (p["name"] or p["job"] or p["likes"] or p["about"]):
        return ""
    lines = ["Qui est l'utilisateur :"]
    if p["name"]:
        lines.append(f"- Prénom : {p['name']} ({PROFILE_GENDERS.get(p['gender'], PROFILE_GENDERS[''])})")
    address = "Tutoie l'utilisateur" if p["address"] == "tu" else "Vouvoie l'utilisateur"
    lines.append(f"- {address} et réponds en {LANGUAGES.get(p['language'], 'français')}.")
    if p["job"]:
        lines.append(f"- Métier et niveau : {p['job']}")
    if p["likes"]:
        lines.append(f"- Ce que l'utilisateur apprécie dans une réponse : {p['likes']}")
    if p["about"]:
        lines.append(f"- À propos : {p['about']}")
    lines.append("Sers-toi de ces informations par défaut (exemples, clins d'œil) sans les réciter. "
                 "Si la conversation contredit cette fiche, c'est la conversation qui a raison.")
    return "\n".join(lines)


def prompt_blocks(conv, folder=False):
    """(bloc persona, bloc utilisateur) pour le prompt système de la conversation."""
    persona = for_conversation(conv)
    profile = get_profile()
    persona_text = render(persona, register_for(persona)) if persona else ""
    profile_text = render_profile(profile) if (not folder or profile.get("in_folder", True)) else ""
    return persona_text, profile_text
