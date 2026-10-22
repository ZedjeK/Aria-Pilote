"""Utilitaires communs aux vérificateurs des tâches (check.py)."""

import hashlib
import shutil
import subprocess
import sys
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
