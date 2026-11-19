"""@fichier : taper « @ » dans la saisie propose les fichiers du dossier attaché ; à l'envoi, chaque @chemin qui désigne
un vrai fichier du dossier est joint au message comme une pièce jointe (texte balisé comme une donnée).

Rien ne sort du dossier de travail (liens et jonctions résolus), rien qui ressemble à un secret (.env, clés), et un
@mot qui n'est pas un fichier (un @pseudo, une adresse) reste du texte.
"""

import os
import re
import time
from pathlib import Path, PurePath

from . import attachments, bac_a_sable as bas, workspace

# Un @ qui commence un mot, pas celui d'une adresse (« jean@exemple.fr ») ; « @"nom avec espace.txt" » entre guillemets
MENTION_RE = re.compile(r'(?<![\w@./\-])@(?:"([^"\n]+)"|([^\s"@]+))')
TRAILING = ".,;:!?)]}»'\""
SECRET_NAMES = {".npmrc", ".pypirc", ".netrc", "id_rsa", "id_ed25519", "id_ecdsa", "credentials"}
SECRET_SUFFIXES = {".pem", ".key", ".pfx", ".p12", ".kdbx", ".env"}  # « .env » : prod.env, comme .env et .env.local
LISTING_TTL = 15  # secondes : un git ls-files par frappe serait trop cher sur un gros dépôt
MAX_LISTED = 20_000
SUGGESTIONS = 8
_cache = {}


class MentionError(Exception):
    pass


def is_secret(name):
    base = PurePath(name).name.lower()
    return base.startswith(".env") or base in SECRET_NAMES or PurePath(base).suffix in SECRET_SUFFIXES


def _usable(name):
    parts = name.split("/")
    return (attachments.supported(parts[-1]) and not is_secret(name)
            and not (bas.PRUNED_DIRS & set(parts[:-1])))


def _inside(root_real, path):
    real = os.path.realpath(path)
    return real == root_real or real.startswith(root_real + os.sep)


def _walk(root):
    root_real = os.path.realpath(root)
    names = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in bas.PRUNED_DIRS and _inside(root_real, os.path.join(dirpath, d)))
        names += [os.path.relpath(os.path.join(dirpath, f), root) for f in sorted(filenames)]
        if len(names) > MAX_LISTED:
            break
    return names


def listing(root):
    """Chemins relatifs (avec /) des fichiers qu'on peut joindre : ce que git ne ignore pas si le dossier est un dépôt,
    sinon le parcours habituel de l'agent. Gardée quelques secondes."""
    root = Path(workspace.validate_root(root))
    hit = _cache.get(str(root))
    if hit and time.monotonic() - hit[0] < LISTING_TTL:
        return hit[1]
    names = bas.git_files(root)
    if names is None:
        names = _walk(root)
    names = [n for n in (x.replace("\\", "/") for x in names) if _usable(n)][:MAX_LISTED]
    if len(_cache) > 8:
        _cache.clear()
    _cache[str(root)] = (time.monotonic(), names)
    return names


def _rank(name, query):
    base = name.rsplit("/", 1)[-1].lower()
    low = name.lower()
    if base.startswith(query):
        return 0
    if query in base:
        return 1
    if query in low:
        return 2
    return None


def candidates(root, query="", limit=SUGGESTIONS):
    """Les fichiers à proposer pour ce qui suit le « @ » : nom qui commence par la saisie, puis qui la contient, puis
    chemin qui la contient ; les plus courts d'abord. Sans saisie : les fichiers les moins profonds."""
    root = Path(workspace.validate_root(root))
    query = query.strip().lower().replace("\\", "/")
    ranked = []
    for name in listing(root):
        rank = 0 if not query else _rank(name, query)
        if rank is not None:
            ranked.append((rank, name.count("/"), len(name), name))
    ranked.sort()
    out = []
    for *_, name in ranked:
        path = root / name
        if path.is_file() and not path.is_symlink():  # git garde parfois un fichier supprimé dans sa liste
            out.append({"path": name, "size": path.stat().st_size})
            if len(out) >= limit:
                break
    return out


def tokens(content):
    """Les chemins cités avec @ dans un texte, sans doublon, dans l'ordre."""
    found = []
    for match in MENTION_RE.finditer(content or ""):
        raw = match.group(1) if match.group(1) is not None else match.group(2).rstrip(TRAILING)
        raw = raw.replace("\\", "/").strip()
        if raw and raw not in found:
            found.append(raw)
    return found


def read(root, content, already=0):
    """Pièces jointes des @chemin d'un message, au format de attachments.extract. `already` : fichiers déjà joints par
    ailleurs (📎), qui comptent dans la limite. Un @mot qui n'est pas un fichier est ignoré ; un fichier qui existe mais
    qu'on ne peut pas joindre est refusé en le disant."""
    if not root:
        return []
    files = []
    for rel in tokens(content):
        try:
            path = workspace.resolve(root, rel)
        except workspace.WorkspaceError:
            raise MentionError(f"« @{rel} » est en dehors du dossier de travail.")
        if not path.is_file():
            continue  # un @pseudo, un dossier : du texte
        name = PurePath(rel).name
        if is_secret(rel) or is_secret(path.name):
            raise MentionError(f"« @{name} » ressemble à un secret (clé, .env…) : Aria ne le joint pas d'elle-même. "
                               "Joins-le toi-même avec 📎 si tu le veux vraiment.")
        if len(files) + already >= attachments.MAX_PER_MESSAGE:
            raise MentionError(f"{attachments.MAX_PER_MESSAGE} fichiers au plus par message "
                               "(pièces jointes et @fichier ensemble).")
        try:
            if path.stat().st_size > attachments.MAX_BYTES:
                raise attachments.AttachmentError(f"« {name} » : fichier trop gros "
                                                  f"({attachments.MAX_BYTES // (1024 * 1024)} Mo au plus).")
            files.append(attachments.extract_bytes(name, path.read_bytes(), label=rel))
        except OSError as exc:
            raise MentionError(f"« @{name} » : lecture impossible ({exc.strerror or exc}).")
        except attachments.AttachmentError as exc:
            raise MentionError(str(exc))
    return files
