"""Compétences (skills) : des instructions réutilisables que l'agent charge à la demande.

Une compétence est un dossier <nom>/SKILL.md : un en-tête « --- » (name, description, registre suggéré),
puis les instructions, de préférence selon le modèle (sections Objectif, Ne pas l'utiliser si, Méthode, Bonnes
pratiques, Format de la réponse, Règles, Vérifier avant de répondre, Exemple) : le formulaire des Réglages le lit et
l'écrit. Les compétences d'avant le modèle (texte libre) restent lues telles quelles.
Trois emplacements, du plus prioritaire au moins prioritaire :
- projet : <dossier de travail>/.aria/skills/
- perso  : <appli>/skills/ (conservé d'une version à l'autre)
- intégré : aria_pilote/skills/ (livré avec l'appli)
"""

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from . import config, trash, workspace

BUILTIN_DIR = Path(__file__).parent / "skills"
USER_DIR = config.ROOT / "skills"
PROJECT_SUBDIR = Path(".aria") / "skills"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
REGISTERS = ("naturel", "sobre", "professionnel")
DEFAULT_REGISTER = "sobre"
MAX_SKILL_CHARS = 20000
MAX_LISTED = 30


class SkillError(Exception):
    pass


@dataclass
class Skill:
    name: str
    description: str
    source: str  # projet | perso | intégrée
    folder: Path
    registre: str = DEFAULT_REGISTER  # registre du persona suggéré pour cette compétence
    objectif: str = ""

    @property
    def editable(self):
        return self.source == "perso"


def _parse(text):
    """En-tête « --- clé: valeur --- » puis corps."""
    meta, body = {}, text
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for i, line in enumerate(lines[1:], 1):
            if line.strip() == "---":
                body = "\n".join(lines[i + 1:]).strip()
                break
            key, _, value = line.partition(":")
            meta[key.strip().lower()] = value.strip().strip("\"'")
    return meta, body


def _scan(base, source):
    found = {}
    if not base.is_dir():
        return found
    for skill_md in sorted(base.glob("*/SKILL.md")):
        try:
            meta, _ = _parse(skill_md.read_text(encoding="utf-8-sig", errors="replace")[:MAX_SKILL_CHARS])
        except OSError:
            continue
        name = meta.get("name") or skill_md.parent.name
        if NAME_RE.match(name):
            registre = meta.get("registre", DEFAULT_REGISTER)
            fields = parse_markdown(skill_md.read_text(encoding="utf-8-sig", errors="replace")[:MAX_SKILL_CHARS])
            found[name] = Skill(name, meta.get("description", "")[:300], source, skill_md.parent,
                                registre if registre in REGISTERS else DEFAULT_REGISTER,
                                (fields or {}).get("objectif", "")[:300])
    return found


def discover(root=None):
    """Compétences visibles, par nom : celles du projet masquent les perso, qui masquent les intégrées."""
    found = _scan(BUILTIN_DIR, "intégrée")
    found.update(_scan(USER_DIR, "perso"))
    if root:
        try:
            found.update(_scan(workspace.validate_root(root) / PROJECT_SUBDIR, "projet"))
        except workspace.WorkspaceError:
            pass
    return dict(sorted(found.items()))


def normalize(name):
    """« Créatif », « creatif », « Revue code » -> nom de dossier (les petits modèles remettent parfois l'accent)."""
    plain = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    return "-".join(plain.lower().split())


def get(name, root=None):
    found = discover(root)
    skill = found.get(name) or found.get(normalize(name))
    if skill is None:
        raise SkillError(f"Compétence inconnue : {name}.")
    return skill


def body(skill):
    _, text = _parse((skill.folder / "SKILL.md").read_text(encoding="utf-8-sig", errors="replace"))
    return text[:MAX_SKILL_CHARS]


def extra_files(skill):
    return sorted(p.relative_to(skill.folder).as_posix()
                  for p in skill.folder.rglob("*") if p.is_file() and p.name != "SKILL.md")


def read_extra(skill, rel):
    """Fichier d'appoint d'une compétence, sans sortir de son dossier."""
    target = (skill.folder / rel).resolve()
    if skill.folder.resolve() not in target.parents or not target.is_file():
        raise SkillError(f"Fichier introuvable dans la compétence « {skill.name} » : {rel}")
    return target.read_text(encoding="utf-8-sig", errors="replace")[:MAX_SKILL_CHARS]


def prompt_section(root=None):
    """Liste des compétences pour le prompt système : nom, quand la charger et son objectif. C'est d'après elle que le
    modèle choisit tout seul (aucun déclencheur à écrire) ; « /nom » dans le chat force une compétence."""
    skills = list(discover(root).values())[:MAX_LISTED]
    if not skills:
        return ""
    lines = [f"- {s.name} : {s.description}" + (f" Objectif : {s.objectif}" if s.objectif else "") for s in skills]
    return ("Compétences : avant de répondre, regarde si la demande correspond à l'une d'elles. Si oui, charge-la "
            "d'abord avec load_skill, puis suis ses instructions ; si plusieurs conviennent, prends la plus précise. "
            "Pour une simple conversation ou une question rapide, n'en charge aucune.\n" + "\n".join(lines))


def expand(name, request, root=None):
    """Message envoyé au modèle pour un « /nom demande » tapé dans le chat."""
    try:
        skill = get(name, root)
    except SkillError:
        return request
    return f"[Compétence « {skill.name} » demandée par l'utilisateur]\n{body(skill)}\n\n[Demande]\n{request or '(aucune précision)'}"


def to_dict(skill, with_body=False):
    data = {"name": skill.name, "description": skill.description, "source": skill.source, "editable": skill.editable,
            "registre": skill.registre}
    if with_body:
        text = (skill.folder / "SKILL.md").read_text(encoding="utf-8-sig", errors="replace")[:MAX_SKILL_CHARS]
        data["body"] = body(skill)
        data["files"] = extra_files(skill)
        data["markdown"] = text
        data["fields"] = parse_markdown(text)  # None : compétence en texte libre, éditable en mode texte seulement
    return data


# --- Modèle de compétence : formulaire <-> fichier ----------------------------------------------------

SECTIONS = {
    "objectif": "Objectif",
    "eviter": "Ne pas l'utiliser si",
    "methode": "Méthode",
    "pratiques": "Bonnes pratiques",
    "format": "Format de la réponse",
    "regles": "Règles",
    "verifier": "Vérifier avant de répondre",
    "exemple": "Exemple",
}
TITLES = {title.lower(): key for key, title in SECTIONS.items()}
RULES_TITLES = {"à faire toujours": "toujours", "à ne jamais faire": "jamais"}


def empty_fields():
    return {"name": "", "description": "", "registre": DEFAULT_REGISTER, "titre": "", "objectif": "", "eviter": [],
            "methode": [{"cas": "", "etapes": []}], "pratiques": [], "format": "",
            "regles": {"toujours": [], "jamais": []}, "verifier": [], "exemple": {"demande": "", "reponse": ""}}


def _lines(items):
    return [" ".join(str(i).split()) for i in items or [] if str(i).strip()]


def render_markdown(fields):
    """Fichier SKILL.md généré à partir des cases du formulaire (sections vides omises)."""
    f = {**empty_fields(), **(fields or {})}
    name, description = f["name"].strip(), " ".join(str(f["description"]).split())
    registre = f["registre"] if f["registre"] in REGISTERS else DEFAULT_REGISTER
    out = ["---", f"name: {name}", f"description: {description}", f"registre: {registre}", "---",
           f"# {f['titre'].strip() or name.replace('-', ' ').capitalize()}", ""]
    if str(f["objectif"]).strip():
        out += ["## Objectif", " ".join(str(f["objectif"]).split()), ""]
    if _lines(f["eviter"]):
        out += ["## Ne pas l'utiliser si", *[f"- {line}" for line in _lines(f["eviter"])], ""]
    cases = [c for c in f["methode"] or [] if _lines(c.get("etapes"))]
    if cases:
        out.append("## Méthode")
        for case in cases:
            if (case.get("cas") or "").strip():
                out.append(f"### {' '.join(case['cas'].split())}")
            out += [f"{n}. {step}" for n, step in enumerate(_lines(case["etapes"]), 1)]
        out.append("")
    if _lines(f["pratiques"]):
        out += ["## Bonnes pratiques", *[f"- {line}" for line in _lines(f["pratiques"])], ""]
    if str(f["format"]).strip():
        out += ["## Format de la réponse", str(f["format"]).strip(), ""]
    rules = f["regles"] or {}
    if _lines(rules.get("toujours")) or _lines(rules.get("jamais")):
        out.append("## Règles")
        if _lines(rules.get("toujours")):
            out += ["### À faire toujours", *[f"- {line}" for line in _lines(rules["toujours"])]]
        if _lines(rules.get("jamais")):
            out += ["### À ne jamais faire", *[f"- {line}" for line in _lines(rules["jamais"])]]
        out.append("")
    if _lines(f["verifier"]):
        out += ["## Vérifier avant de répondre", *[f"- [ ] {line}" for line in _lines(f["verifier"])], ""]
    example = f["exemple"] or {}
    if str(example.get("demande", "")).strip():
        out += ["## Exemple", f"> Demande : {' '.join(example['demande'].split())}",
                f"> Réponse attendue (résumé) : {' '.join(str(example.get('reponse', '')).split())}", ""]
    return "\n".join(out).rstrip() + "\n"


def parse_markdown(text):
    """Cases du formulaire lues dans un SKILL.md au format du modèle, ou None s'il est en texte libre."""
    meta, rest = _parse(text)
    fields = empty_fields()
    fields.update(name=meta.get("name", ""), description=meta.get("description", ""),
                  registre=meta.get("registre", DEFAULT_REGISTER) if meta.get("registre") in REGISTERS else DEFAULT_REGISTER)
    lines = rest.splitlines()
    if lines and lines[0].startswith("# "):
        fields["titre"] = lines.pop(0)[2:].strip()
    section, sub, found = None, None, False
    preamble = []
    fields["methode"] = []
    for raw in lines:
        line = raw.rstrip()
        if line.startswith("## "):
            section = TITLES.get(line[3:].strip().lower())
            if section is None:
                return None  # section inconnue : on ne casse pas le texte de l'auteur, mode texte seulement
            found, sub = True, None
            continue
        if not line.strip():
            continue
        if section is None:
            preamble.append(line)
            continue
        if line.startswith("### "):
            sub = line[4:].strip()
            if section == "methode":
                fields["methode"].append({"cas": sub, "etapes": []})
            elif section == "regles":
                sub = RULES_TITLES.get(sub.lower())
                if sub is None:
                    return None
            continue
        item = line.strip()
        if section == "objectif":
            fields["objectif"] = f"{fields['objectif']} {item}".strip()
        elif section == "format":
            fields["format"] = f"{fields['format']}\n{line}".strip("\n")
        elif section in ("eviter", "pratiques") and item.startswith("- "):
            fields[section].append(item[2:].strip())
        elif section == "verifier" and item.startswith("- "):
            fields["verifier"].append(re.sub(r"^\[[ x]\]\s*", "", item[2:].strip()))
        elif section == "methode" and re.match(r"^\d+\.\s", item):
            if not fields["methode"]:
                fields["methode"].append({"cas": "", "etapes": []})
            fields["methode"][-1]["etapes"].append(re.sub(r"^\d+\.\s+", "", item))
        elif section == "regles" and sub and item.startswith("- "):
            fields["regles"][sub].append(item[2:].strip())
        elif section == "exemple" and item.startswith(">"):
            content = item.lstrip("> ").strip()
            key, _, value = content.partition(" : ")
            if key.lower().startswith("demande"):
                fields["exemple"]["demande"] = value.strip()
            elif key.lower().startswith("réponse"):
                fields["exemple"]["reponse"] = value.strip()
            else:
                return None
        else:
            return None  # ligne hors du modèle (paragraphe libre…) : mode texte seulement
    if not found or preamble:
        return None
    if not fields["methode"]:
        fields["methode"] = [{"cas": "", "etapes": []}]
    return fields


def save_user_skill(name, description, text):
    """Compétence perso écrite en mode texte (en-tête facultatif) : son nom et sa description font foi."""
    if not NAME_RE.match(name or ""):
        raise SkillError("Nom invalide : minuscules, chiffres et tirets uniquement (ex. revue-ansible).")
    meta, body_text = _parse(text or "")
    description = " ".join((description or meta.get("description", "")).split())
    if not description:
        raise SkillError("La description est obligatoire : c'est elle qui dit à l'agent quand s'en servir.")
    registre = meta.get("registre", DEFAULT_REGISTER)
    registre = registre if registre in REGISTERS else DEFAULT_REGISTER
    folder = USER_DIR / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\nregistre: {registre}\n---\n\n{body_text.strip()}\n", encoding="utf-8")
    return discover().get(name) or Skill(name, description, "perso", folder, registre)


def save_user_fields(fields):
    """Compétence perso écrite avec le formulaire : le fichier est généré à partir des cases."""
    name = (fields or {}).get("name", "").strip()
    if not NAME_RE.match(name):
        raise SkillError("Nom invalide : minuscules, chiffres et tirets uniquement (ex. revue-ansible).")
    if not " ".join(str(fields.get("description", "")).split()):
        raise SkillError("« Quand la charger » est obligatoire : c'est d'après elle que le modèle choisit.")
    folder = USER_DIR / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(render_markdown(fields), encoding="utf-8")
    return discover().get(name)


def customize(name):
    """« Personnaliser » une compétence intégrée : une copie perso modifiable, qui la remplace."""
    builtin = _scan(BUILTIN_DIR, "intégrée").get(name)
    if builtin is None:
        raise SkillError("Seule une compétence intégrée se personnalise.")
    target = USER_DIR / name
    if (target / "SKILL.md").exists():
        raise SkillError("Tu as déjà une version perso de cette compétence.")
    target.mkdir(parents=True, exist_ok=True)
    for path in builtin.folder.rglob("*"):
        if path.is_file():
            dest = target / path.relative_to(builtin.folder)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(path.read_bytes())
    return discover()[name]


def delete_user_skill(name):
    """Envoie une compétence perso à la corbeille (récupérable)."""
    folder = USER_DIR / name
    if not NAME_RE.match(name or "") or not (folder / "SKILL.md").exists():
        raise SkillError("Seules tes compétences perso peuvent être supprimées.")
    trash.to_recycle_bin([folder])
