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
import shutil
import subprocess
from pathlib import Path

from . import config

DEFAULT_PROTECTED = ["main", "master"]
SKILLS = ("github-pr", "github-issues", "github-release")
BRANCH_RE = re.compile(r"^[\w.\-/]{1,60}$")  # un nom de branche ou un préfixe (« feat/ »), sans espace ni caractère spécial
MERGE_METHODS = ("squash", "merge", "rebase")
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


def settings():
    return {**config.DEFAULTS["github"], **(config.get().get("github") or {})}


def protected_branches():
    branches = settings().get("protected_branches")
    return list(DEFAULT_PROTECTED if branches is None else branches)


def skills_enabled():
    return bool(settings()["skills"])


def conventions():
    """Les choix de Réglages › GitHub, ajoutés à une compétence github-* quand le modèle la charge."""
    s = settings()
    protected = ", ".join(s["protected_branches"]) or "aucune"
    lines = [f"- Branches de travail : {s['feature_prefix']}sujet-court pour une nouveauté, {s['fix_prefix']}sujet-court "
             "pour un correctif.",
             f"- Fusion d'une pull request : gh pr merge <n> --{s['merge_method']}.",
             f"- Branches protégées, sans push direct (Aria le refuse) : {protected}."
             + (" Ne commite jamais dessus : crée la branche de travail avant de modifier un fichier."
                if s["protected_branches"] else "")]
    if s["attribution"]:
        lines.append(f"- Termine chaque message de commit et chaque corps de pull request par cette ligne, telle "
                     f"quelle : {s['attribution']}")
    return "\n\nRéglages de l'utilisateur (Réglages › GitHub), prioritaires sur les exemples ci-dessus :\n" + "\n".join(lines)


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


# --- lectures sans demander (Réglages › GitHub, « read_auto ») ------------------------------------

# Sous-commandes git qui ne font que lire, et les options qui les rendent dangereuses (écrire un fichier, lancer un
# programme). Une option globale (git -c …) n'est jamais permise : -c core.pager=… lancerait n'importe quoi.
GIT_AUTO = {"status", "log", "diff", "show", "fetch", "branch", "remote", "rev-parse", "describe", "tag", "blame",
            "shortlog", "ls-files"}
GIT_AUTO_DENIED = ("--output", "--upload-pack", "--exec", "--ext-diff", "--textconv")
# git branch, tag et remote ne lisent qu'avec ces options seules (« git branch nom » crée une branche)
GIT_LIST_ONLY = {"branch": {"-a", "--all", "-r", "--remotes", "-v", "-vv", "--list", "--show-current"},
                 "tag": {"-l", "--list"}, "remote": {"-v", "--verbose"}}
GH_AUTO = {("pr", "view"), ("pr", "list"), ("pr", "checks"), ("pr", "diff"), ("pr", "status"), ("issue", "view"),
           ("issue", "list"), ("issue", "status"), ("run", "view"), ("run", "list"), ("repo", "view"),
           ("release", "list"), ("release", "view"), ("label", "list"), ("auth", "status")}


def read_only(command):
    """Vrai si la commande est une seule lecture git ou gh de la liste (lancée sans demander si c'est activé)."""
    from .permissions import OPERATORS  # permissions importe ce module
    if OPERATORS.search(command or ""):
        return False
    found = segments(command)
    if len(found) != 1:
        return False
    tool, args = found[0]
    if tool == "git":
        if not args or args[0].startswith("-") or args[0].lower() not in GIT_AUTO:
            return False
        sub, rest = args[0].lower(), args[1:]
        if any(a.lower().startswith(GIT_AUTO_DENIED) for a in rest):
            return False
        if sub == "fetch" and any(":" in a for a in rest):  # « git fetch origin main:main » écrit une branche
            return False
        return sub not in GIT_LIST_ONLY or all(a in GIT_LIST_ONLY[sub] for a in rest)
    words = _gh_words(args)
    if any(a in ("--web", "-w") for a in args):  # ouvrirait le navigateur
        return False
    return words[:1] == ["search"] or tuple(words[:2]) in GH_AUTO


# --- état de git et gh (Réglages › GitHub) --------------------------------------------------------

def _run(args, timeout=15):
    """(code, sortie) d'un programme, ou None s'il ne se lance pas."""
    try:
        out = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None
    return out.returncode, (out.stdout or "") + (out.stderr or "")


def status():
    """git et gh installés, identité des commits, compte GitHub connecté. Le jeton n'est jamais lu ni renvoyé."""
    result = {"git": None, "gh": None}
    git = shutil.which("git")
    if git:
        ran = _run([git, "--version"])
        name = _run([git, "config", "--global", "--get", "user.name"])
        email = _run([git, "config", "--global", "--get", "user.email"])
        result["git"] = {"version": ran[1].strip().removeprefix("git version ") if ran else "?",
                         "user_name": name[1].strip() if name and name[0] == 0 else "",
                         "user_email": email[1].strip() if email and email[0] == 0 else ""}
    gh = shutil.which("gh")
    if gh:
        ran = _run([gh, "--version"])
        version = re.search(r"gh version (\S+)", ran[1]) if ran else None
        auth = _run([gh, "auth", "status", "--active"], timeout=20)
        text = auth[1] if auth else ""
        accounts = [{"host": h, "login": login} for h, login in re.findall(r"Logged in to (\S+) account (\S+)", text)]
        scopes = re.search(r"Token scopes: (.*)", text)
        result["gh"] = {"version": version.group(1) if version else "?", "accounts": accounts,
                        "scopes": re.findall(r"'([^']+)'", scopes.group(1)) if scopes else [],
                        "error": "" if accounts else ("Connexion refusée par GitHub : relance gh auth login."
                                                      if "Failed to log in" in text else "Pas connecté à GitHub.")}
    return result


def check_settings(patch):
    """Réglages › GitHub vérifiés : ValueError avec un message lisible si une valeur ne va pas."""
    out = dict(patch)
    branches = out.get("protected_branches")
    if branches is not None:
        if isinstance(branches, str):  # « main, master » tapé dans le champ
            branches = re.split(r"[\s,]+", branches)
        branches = list(dict.fromkeys(b.strip() for b in branches if b and b.strip()))
        bad = [b for b in branches if not BRANCH_RE.match(b) or b.startswith("-")]
        if bad:
            raise ValueError(f"Nom de branche invalide : {bad[0]}")
        if len(branches) > 20:
            raise ValueError("20 branches protégées au plus.")
        out["protected_branches"] = branches
    for key in ("feature_prefix", "fix_prefix"):
        if out.get(key) is not None:
            out[key] = out[key].strip()
            if out[key] and (not BRANCH_RE.match(out[key]) or out[key].startswith("-")):
                raise ValueError(f"Préfixe de branche invalide : {out[key]}")
    if out.get("merge_method") is not None and out["merge_method"] not in MERGE_METHODS:
        raise ValueError(f"Mode de fusion inconnu : {out['merge_method']}")
    if out.get("attribution") is not None:
        out["attribution"] = " ".join(out["attribution"].split())  # une seule ligne
    return out
