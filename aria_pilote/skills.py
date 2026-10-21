"""Compétences (skills) : des instructions réutilisables que l'agent charge à la demande.

Une compétence est un dossier <nom>/SKILL.md : un en-tête « --- » avec name et description,
puis les instructions. Trois emplacements, du plus prioritaire au moins prioritaire :
- projet : <dossier de travail>/.aria/skills/
- perso  : <appli>/skills/ (conservé d'une version à l'autre)
- intégré : aria_pilote/skills/ (livré avec l'appli)
"""

import re
from dataclasses import dataclass
from pathlib import Path

from . import config, trash, workspace

BUILTIN_DIR = Path(__file__).parent / "skills"
USER_DIR = config.ROOT / "skills"
PROJECT_SUBDIR = Path(".aria") / "skills"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
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
            found[name] = Skill(name, meta.get("description", "")[:300], source, skill_md.parent)
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


def get(name, root=None):
    skill = discover(root).get(name)
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
    """Liste courte des compétences, pour le prompt système de l'agent."""
    skills = list(discover(root).values())[:MAX_LISTED]
    if not skills:
        return ""
    lines = [f"- {s.name} ({s.source}) : {s.description}" for s in skills]
    return ("Compétences disponibles : quand une tâche correspond à l'une d'elles, charge-la avec load_skill "
            "avant d'agir, puis suis ses instructions.\n" + "\n".join(lines))


def expand(name, request, root=None):
    """Message envoyé au modèle pour un « /nom demande » tapé dans le chat."""
    try:
        skill = get(name, root)
    except SkillError:
        return request
    return f"[Compétence « {skill.name} » demandée par l'utilisateur]\n{body(skill)}\n\n[Demande]\n{request or '(aucune précision)'}"


def to_dict(skill, with_body=False):
    data = {"name": skill.name, "description": skill.description, "source": skill.source, "editable": skill.editable}
    if with_body:
        data["body"] = body(skill)
        data["files"] = extra_files(skill)
    return data


def save_user_skill(name, description, text):
    if not NAME_RE.match(name or ""):
        raise SkillError("Nom invalide : minuscules, chiffres et tirets uniquement (ex. revue-ansible).")
    if not (description or "").strip():
        raise SkillError("La description est obligatoire : c'est elle qui dit à l'agent quand s'en servir.")
    folder = USER_DIR / name
    folder.mkdir(parents=True, exist_ok=True)
    description = " ".join(description.split())
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n\n{text.strip()}\n", encoding="utf-8")
    return Skill(name, description, "perso", folder)


def delete_user_skill(name):
    """Envoie une compétence perso à la corbeille (récupérable)."""
    folder = USER_DIR / name
    if not NAME_RE.match(name or "") or not (folder / "SKILL.md").exists():
        raise SkillError("Seules tes compétences perso peuvent être supprimées.")
    trash.to_recycle_bin([folder])
