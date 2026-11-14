"""Retour des fichiers du bac à sable vers le projet réel : ce qui a changé dans la copie, et ce qui peut revenir.

Tout ce qui vient du bac à sable est non fiable : l'agent y a pu lancer du code arbitraire, donc les noms de fichiers
comme leur contenu sont traités comme venant d'un inconnu.
- Un nom que Windows interpréterait autrement est refusé (« f.txt:cache » créerait un flux de données caché sur NTFS,
  « NUL » ou « CON » désignent des périphériques), de même qu'un chemin qui sort du projet ou touche .git ou .aria (les
  hooks de projet s'exécutent sur le PC de l'utilisateur).
- Un fichier que l'utilisateur a modifié de son côté depuis la copie n'est jamais écrasé : c'est un conflit.
- Ni binaires, ni fichiers non UTF-8, ni caches, ni ce que git ignore ne reviennent. Les suppressions ne sont pas
  répercutées : elles sont signalées.
Le contenu n'est jamais déroulé sur le disque : il est lu en mémoire, puis écrit par le circuit habituel des écritures
(accord, sauvegarde, « Annuler »).
"""

import hashlib
import shutil
import subprocess
from pathlib import Path

from . import bac_a_sable as bas, workspace

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_FILES = 30  # par retour : un accord reste lisible
MAX_TOTAL_BYTES = 20 * 1024 * 1024
PROTECTED = {".git", ".aria"}  # n'importe où dans le chemin
IGNORED_DIRS = bas.SKIP_DIRS | {".pytest_cache", ".ruff_cache", ".tox", ".nox"}
IGNORED_SUFFIXES = (".pyc", ".pyo")
RESERVED = {"con", "prn", "aux", "nul", "conin$", "conout$"} | {f"com{i}" for i in range(1, 10)} \
    | {f"lpt{i}" for i in range(1, 10)}
BAD_CHARS = set('<>:"|?*\\')
MAX_PATH_CHARS = 240


def name_refusal(name):
    """Raison pour laquelle ce chemin ne peut pas être écrit sur le PC de l'utilisateur, ou None."""
    parts = name.split("/")
    if name.startswith("/") or any(p in ("", ".", "..") for p in parts):
        return "chemin invalide"
    if len(name) > MAX_PATH_CHARS:
        return "chemin trop long"
    if PROTECTED & set(parts):
        return "dossier protégé (.git, .aria)"
    for part in parts:
        if any(c in BAD_CHARS or ord(c) < 32 for c in part):
            return "nom que Windows interpréterait autrement (caractère interdit, flux de données caché)"
        if part != part.rstrip(" ."):
            return "nom qui finit par un point ou une espace (Windows le tronquerait)"
        if part.split(".")[0].casefold() in RESERVED:
            return "nom réservé par Windows (périphérique)"
    return None


def _ignored_by_default(name):
    return bool(IGNORED_DIRS & set(name.split("/")[:-1])) or name.endswith(IGNORED_SUFFIXES)


def git_ignored(root, names):
    """Parmi `names`, ceux que .gitignore écarte (caches, journaux, résultats de compilation, .env…)."""
    root = Path(root)
    if not names or not (root / ".git").exists() or shutil.which("git") is None:
        return set()
    try:
        proc = subprocess.run(["git", "-C", str(root), "check-ignore", "--stdin", "-z"],
                              input="\0".join(names).encode("utf-8"), capture_output=True, timeout=60,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return set()
    return {n for n in proc.stdout.decode("utf-8", errors="replace").split("\0") if n}  # code 1 : rien d'ignoré


def scan(cid, root):
    """Compare la copie du bac à sable à ce qui y avait été copié. Renvoie un dict :
    modifies, ajoutes (chemins qui peuvent revenir), supprimes, conflits {chemin: raison}, refuses {chemin: raison},
    ignores (nombre de fichiers écartés : caches et ce que git ignore)."""
    manifest = bas.load_manifest(cid)
    if manifest is None:
        raise bas.BacASableError("Les notes de la copie manquent : refais la copie (sandbox_reset).")
    listing = bas.sandbox_listing(cid)
    root = Path(workspace.validate_root(root))
    out = {"modifies": [], "ajoutes": [], "supprimes": [], "conflits": {}, "refuses": {}, "ignores": 0}
    candidates = []
    for name, (digest, size) in sorted(listing.items()):
        if _ignored_by_default(name):
            out["ignores"] += 1
            continue
        known = manifest.get(name)
        if known is not None and digest == known["sandbox"]:
            continue  # inchangé
        candidates.append((name, size, known))
    for name in sorted(set(manifest) - set(listing)):
        if not _ignored_by_default(name):
            out["supprimes"].append(name)
    ignored_new = git_ignored(root, [n for n, _, known in candidates if known is None])
    for name, size, known in candidates:
        if name in ignored_new:
            out["ignores"] += 1
            continue
        reason = name_refusal(name)
        if reason is None and size > MAX_FILE_BYTES:
            reason = f"trop gros ({size // 1024} Ko, limite {MAX_FILE_BYTES // 1024 // 1024} Mo)"
        if reason is None:
            try:
                target = workspace.resolve(root, name)
            except workspace.WorkspaceError as exc:
                reason = str(exc)
        if reason is not None:
            out["refuses"][name] = reason
            continue
        if known is None:  # nouveau dans le bac à sable
            if target.exists():
                out["conflits"][name] = "existe déjà dans ton projet (créé de ton côté depuis la copie)"
                continue
            out["ajoutes"].append(name)
        else:
            if not target.is_file():
                out["conflits"][name] = "supprimé de ton côté depuis la copie"
                continue
            if hashlib.sha256(target.read_bytes()).hexdigest() != known["real"]:
                out["conflits"][name] = "modifié de ton côté depuis la copie"
                continue
            out["modifies"].append(name)
    return out


def read_texts(cid, names):
    """Contenu texte de ces fichiers du bac à sable : ({chemin: (texte, empreinte de ses octets)}, {chemin: raison}).
    Binaire, non UTF-8, disparu entre-temps ou trop volumineux au total : refusé avec sa raison."""
    raw = bas.sandbox_read(cid, names)
    texts, refused, total = {}, {}, 0
    for name in names:
        data = raw.get(name)
        if data is None:
            refused[name] = "disparu du bac à sable entre-temps"
        elif len(data) > MAX_FILE_BYTES:
            refused[name] = "trop gros"
        elif b"\0" in data[:8192]:
            refused[name] = "binaire (non répercuté)"
        elif total + len(data) > MAX_TOTAL_BYTES:
            refused[name] = "dépasse le volume maximal d'un retour : relance pour les suivants"
        else:
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                refused[name] = "pas de l'UTF-8 (non répercuté)"
                continue
            total += len(data)
            texts[name] = (text, hashlib.sha256(data).hexdigest())
    return texts, refused


def mark_applied(cid, entries):
    """Après un retour : le fichier du bac à sable et celui de l'utilisateur redeviennent identiques (à leurs fins de
    ligne près), les prochaines comparaisons partent de là. `entries` : {chemin: (empreinte côté bac, côté projet)}."""
    bas.update_manifest(cid, {name: {"sandbox": s, "real": r} for name, (s, r) in entries.items()})
