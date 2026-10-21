"""Cloisonnement de l'agent : tout chemin doit rester dans le dossier de travail."""

from pathlib import Path


class WorkspaceError(Exception):
    pass


def validate_root(root):
    """Dossier de travail normalisé (liens résolus), ou erreur s'il n'existe pas."""
    try:
        path = Path(root).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        raise WorkspaceError(f"Dossier introuvable : {root}")
    if not path.is_dir():
        raise WorkspaceError(f"Ce n'est pas un dossier : {root}")
    if path == Path(path.anchor):
        raise WorkspaceError("Refusé : le dossier de travail ne peut pas être la racine d'un disque.")
    return path


def resolve(root, rel):
    """Chemin absolu de `rel` dans `root`.

    Les liens symboliques et jonctions sont résolus avant la vérification :
    un lien qui pointe hors du dossier est refusé comme un « ..\\ ».
    """
    root = validate_root(root)
    candidate = Path(rel or ".")
    target = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if target != root and root not in target.parents:
        raise WorkspaceError(f"Refusé : « {rel} » est en dehors du dossier de travail.")
    return target


def relative(root, path):
    rel = Path(path).relative_to(validate_root(root))
    return rel.as_posix() or "."
