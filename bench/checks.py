"""Utilitaires communs aux vérificateurs des tâches (check.py)."""

import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree(root):
    """Empreinte de chaque fichier du dossier (chemins relatifs), caches Python exclus."""
    root = Path(root)
    return {p.relative_to(root).as_posix(): digest(p) for p in root.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts}


def unchanged(ws, run, *names):
    """Les fichiers indiqués (ou tout le dossier si aucun) sont identiques à l'état de départ."""
    if not names:
        return tree(ws) == tree(run["start"])
    return all((Path(ws) / n).is_file() and digest(Path(ws) / n) == digest(run["start"] / n) for n in names)


def python(ws, *args, timeout=60):
    """Lance Python (celui du banc d'essai) dans le dossier de travail ; renvoie (code, sortie)."""
    proc = subprocess.run([sys.executable, "-X", "utf8", *args], cwd=ws, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)
    return proc.returncode, proc.stdout + proc.stderr


def powershell(ws, script, timeout=60):
    exe = shutil.which("pwsh") or "powershell"
    proc = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script],
                          cwd=ws, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return proc.returncode, proc.stdout + proc.stderr


def missing(run, *words):
    """Mots attendus absents de la réponse finale (insensible à la casse)."""
    text = run["final"].lower()
    return [w for w in words if w.lower() not in text]


def used(run, tool):
    return any(c["name"] == tool for c in run["calls"])


# --- dépôts git (tâches GitHub) : un vrai dépôt, et un « origin » nu dans un dossier temporaire, sans réseau ---

START_FILE = ".git/aria-bench-depart"  # le commit de départ, rangé dans .git : hors du suivi de git


def git(cwd, *args):
    """(code, sortie) d'une commande git."""
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=60)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def make_repo(ws):
    """Le dossier devient un dépôt sur main, un commit poussé sur un origin local nu."""
    origin = Path(tempfile.mkdtemp(prefix="aria-bench-origin-"))
    git(origin, "init", "-q", "--bare", "-b", "main")
    git(ws, "init", "-q", "-b", "main")
    git(ws, "config", "user.name", "Banc Aria")
    git(ws, "config", "user.email", "banc@aria.invalid")
    git(ws, "add", "-A")
    git(ws, "commit", "-q", "-m", "Départ")
    git(ws, "remote", "add", "origin", str(origin))
    git(ws, "push", "-q", "-u", "origin", "main")
    (Path(ws) / START_FILE).write_text(git(ws, "rev-parse", "HEAD")[1], encoding="utf-8")


def repo_state(ws):
    """(commit de départ, dossier de l'origin, main local, main de l'origin, autres branches de l'origin)."""
    start = (Path(ws) / START_FILE).read_text(encoding="utf-8").strip()
    origin = git(ws, "remote", "get-url", "origin")[1]
    branches = git(origin, "for-each-ref", "--format=%(refname:short)", "refs/heads")[1].split()
    return (start, origin, git(ws, "rev-parse", "main")[1], git(origin, "rev-parse", "main")[1],
            [b for b in branches if b != "main"])
