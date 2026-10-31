"""Git et GitHub (gh) : les garde-fous des commandes de l'agent.

- Ce qui sort de ton PC (git push, une PR, un commentaire, une release…) est toujours redemandé : aucune règle
  « toujours autoriser » ne le couvre.
- Pas de push direct sur une branche protégée (main et master par défaut, clé « github » de config.json).
Les commandes destructrices (force push, suppressions sur GitHub, lecture du jeton) sont sur la liste noire de tools.
Comme la liste noire, c'est un filet contre un « Autoriser » cliqué trop vite, pas une frontière : PowerShell offre
mille détours.
"""

import re
import shlex
import subprocess
from pathlib import Path

from . import config

DEFAULT_PROTECTED = ["main", "master"]
# Ce qui sépare deux commandes (PowerShell) : chaque morceau est examiné à part
SEPARATORS = re.compile(r"[;\r\n|&]+")
GIT_RE = re.compile(r"^(?:&\s*)?(?:[\w:\\/.\"' -]*[\\/])?git(?:\.exe)?[\"']?$", re.I)
GH_RE = re.compile(r"^(?:&\s*)?(?:[\w:\\/.\"' -]*[\\/])?gh(?:\.exe)?[\"']?$", re.I)
# Options de git qui prennent une valeur (avant la sous-commande, ou après « push »)
GIT_VALUE_OPTS = {"-c", "-C", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
PUSH_VALUE_OPTS = {"-o", "--push-option", "--repo", "--receive-pack", "--exec", "--signed"}
# Sous-commandes gh qui ne font que lire (le reste agit sur GitHub, donc hors de ton PC)
GH_READ = {"list", "view", "status", "checks", "diff", "search", "watch", "download", "clone", "checkout", "browse",
           "get", "help", "--version", "--help", "-h", "version"}
GH_API_WRITE = {"-f", "-F", "--field", "--raw-field", "--input"}


def protected_branches():
    branches = (config.get().get("github") or {}).get("protected_branches")
    return list(DEFAULT_PROTECTED if branches is None else branches)


def _tokens(segment):
    try:
        return shlex.split(segment, posix=False)
    except ValueError:  # guillemet non fermé : découpage simple
        return segment.split()


def _unquote(token):
    return token.strip("\"'")


def segments(command):
    """Les commandes git et gh d'une ligne : [("git" | "gh", [arguments])]."""
    found = []
    for part in SEPARATORS.split(str(command or "")):
        tokens = _tokens(part.strip())
        if tokens and tokens[0] == "&":  # « & 'C:\\…\\git.exe' push »
            tokens = tokens[1:]
        if not tokens:
            continue
        if GIT_RE.match(tokens[0]):
            found.append(("git", [_unquote(t) for t in tokens[1:]]))
        elif GH_RE.match(tokens[0]):
            found.append(("gh", [_unquote(t) for t in tokens[1:]]))
    return found


def _git_sub(args):
    """(sous-commande git, ses arguments, dossier de -C)."""
    folder, i = None, 0
    while i < len(args) and args[i].startswith("-"):
        if args[i] in GIT_VALUE_OPTS:
            if args[i] == "-C" and i + 1 < len(args):
                folder = args[i + 1]
            i += 2
        else:
            i += 1
    return (args[i].lower() if i < len(args) else ""), args[i + 1:], folder


def _gh_words(args):
    """Les mots de la sous-commande gh, sans les options : « gh -R a/b pr create » → ["pr", "create"]."""
    words, i = [], 0
    while i < len(args) and len(words) < 2:
        if args[i] in ("-R", "--repo"):
            i += 2
            continue
        if args[i].startswith("-") and args[i] not in ("--version", "--help", "-h"):
            break
        words.append(args[i].lower())
        i += 1
    return words


def outward(command):
    """Pourquoi cette commande sort de ton PC (« git push », « gh pr create »…), ou None."""
    for tool, args in segments(command):
        if tool == "git":
            sub, _, _ = _git_sub(args)
            if sub == "push":
                return "git push"
            continue
        words = _gh_words(args)
        if not words:
            continue
        if words[0] == "api":
            method = next((args[i + 1].upper() for i, a in enumerate(args[:-1]) if a in ("-X", "--method")), "")
            method = method or next((a.split("=", 1)[1].upper() for a in args if a.startswith("--method=")), "")
            if (method and method != "GET") or any(a.split("=", 1)[0] in GH_API_WRITE for a in args):
                return "gh api (écriture)"
            continue
        if words[0] in GH_READ or (len(words) > 1 and words[1] in GH_READ):
            continue
        return "gh " + " ".join(words)
    return None


def _current_branch(folder):
    try:
        # symbolic-ref plutôt que rev-parse : répond aussi sur un dépôt sans commit ; rien si HEAD est détachée
        out = subprocess.run(["git", "symbolic-ref", "--short", "-q", "HEAD"], cwd=folder, capture_output=True,
                             text=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _push_targets(args, folder):
    """Les branches distantes visées par un « git push » (None : impossible à dire, « * » : toutes)."""
    positional, i = [], 0
    while i < len(args):
        a = args[i]
        if a in ("--all", "--mirror", "--branches"):
            return ["*"]
        if a in PUSH_VALUE_OPTS:
            i += 2
            continue
        if not a.startswith("-"):
            positional.append(a)
        i += 1
    refspecs = positional[1:]
    if not refspecs and "--tags" in args:  # les tags seulement
        return []
    if not refspecs:  # « git push », « git push origin » : la branche en cours
        return [_current_branch(folder)]
    targets = []
    for spec in refspecs:
        spec = spec.lstrip("+")
        dst = spec.split(":", 1)[1] if ":" in spec else spec
        if dst.upper() in ("HEAD", ""):
            dst = _current_branch(folder) if dst else None
        if dst and dst.startswith("refs/heads/"):
            dst = dst[len("refs/heads/"):]
        targets.append(dst)
    return targets


def check_push(command, root):
    """Raison du refus si la commande pousse directement sur une branche protégée, sinon None."""
    protected = {b.lower() for b in protected_branches()}
    if not protected:
        return None
    for tool, args in segments(command):
        if tool != "git":
            continue
        sub, rest, folder = _git_sub(args)
        if sub != "push":
            continue
        where = Path(root) / folder if folder else Path(root)
        for target in _push_targets(rest, where):
            if target == "*":
                return "push de toutes les branches, dont les branches protégées"
            if target and target.lower() in protected:
                return f"push direct sur « {target} », une branche protégée"
    return None
