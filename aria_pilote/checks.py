"""Vérification automatique de la syntaxe d'un fichier que l'agent vient d'écrire.

Le résultat s'ajoute à la réponse de l'outil d'écriture : le modèle voit tout de suite une erreur
(avec la ligne fautive) et peut la corriger. Rien n'est jamais exécuté : on analyse, c'est tout.
"""

import json
import os
import shutil
import subprocess
import tomllib
import xml.etree.ElementTree as ElementTree
from pathlib import Path

from . import config

MAX_BYTES = 2 * 1024 * 1024
POWERSHELL = shutil.which("pwsh") or "powershell.exe"
PS_TIMEOUT_S = 10

# Analyseur officiel de PowerShell : le script n'est pas exécuté, seulement découpé.
# Le chemin passe par une variable d'environnement : ajouté après -Command, il serait lu comme du code
# (et le fichier serait exécuté).
_PS_PARSE = """$erreurs = $null
[void][System.Management.Automation.Language.Parser]::ParseFile($env:ARIA_CHECK_PATH, [ref]$null, [ref]$erreurs)
foreach ($e in $erreurs) { "$($e.Extent.StartLineNumber)`t$($e.Extent.StartColumnNumber)`t$($e.Message)" }"""


class CheckFailed(Exception):
    def __init__(self, message, line=None, column=None):
        super().__init__(message)
        self.line, self.column = line, column


def _python(path, text):
    try:
        compile(text, str(path), "exec", dont_inherit=True)
    except SyntaxError as exc:
        raise CheckFailed(f"{exc.msg} (vérifié avec Python 3.11)", exc.lineno, exc.offset)


def _json(path, text):
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        raise CheckFailed(exc.msg, exc.lineno, exc.colno)


def _toml(path, text):
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise CheckFailed(str(exc))


def _xml(path, text):
    try:
        ElementTree.fromstring(text.encode("utf-8"))
    except ElementTree.ParseError as exc:
        line, column = exc.position
        raise CheckFailed(str(exc).split(":")[0], line, column + 1)


def _yaml(path, text):
    import yaml  # facultatif : sans PyYAML, les fichiers YAML ne sont pas vérifiés

    try:
        list(yaml.safe_load_all(text))
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        message = getattr(exc, "problem", None) or str(exc)
        raise CheckFailed(message, mark.line + 1 if mark else None, mark.column + 1 if mark else None)


def _powershell(path, text):
    proc = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", _PS_PARSE],
                          env={**os.environ, "ARIA_CHECK_PATH": str(path.resolve())},
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=PS_TIMEOUT_S, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    errors = [line.split("\t", 2) for line in proc.stdout.splitlines() if line.count("\t") >= 2]
    if errors:
        line, column, message = errors[0]
        more = f" (et {len(errors) - 1} autre(s) erreur(s))" if len(errors) > 1 else ""
        raise CheckFailed(message + more, int(line), int(column))


CHECKERS = {
    ".py": ("Python", _python), ".pyw": ("Python", _python),
    ".json": ("JSON", _json),
    ".toml": ("TOML", _toml),
    ".xml": ("XML", _xml), ".csproj": ("XML", _xml), ".config": ("XML", _xml), ".svg": ("XML", _xml),
    ".yaml": ("YAML", _yaml), ".yml": ("YAML", _yaml),
    ".ps1": ("PowerShell", _powershell), ".psm1": ("PowerShell", _powershell), ".psd1": ("PowerShell", _powershell),
}


def _excerpt(text, line, column):
    lines = text.splitlines()
    if not line or not 1 <= line <= len(lines):
        return ""
    code = lines[line - 1].replace("\t", "    ")
    pointer = " " * (len(str(line)) + 3 + max(0, (column or 1) - 1)) + "^" if column else ""
    return f"\n   {line} | {code}" + (f"\n   {pointer}" if pointer else "")


def check(path):
    """Ligne à ajouter au résultat de l'écriture, ou None (type inconnu, option désactivée, fichier trop gros).

    Renvoie (texte, ok) : ok vaut False quand la syntaxe est invalide."""
    path = Path(path)
    kind = CHECKERS.get(path.suffix.lower())
    if kind is None or not config.get().get("auto_check", True):
        return None
    label, checker = kind
    try:
        if path.stat().st_size > MAX_BYTES:
            return None
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        checker(path, text)
    except CheckFailed as exc:
        where = f" ligne {exc.line}" + (f", colonne {exc.column}" if exc.column else "") if exc.line else ""
        return f"⚠ Vérification automatique : {label} invalide{where} : {exc}{_excerpt(text, exc.line, exc.column)}", False
    except ImportError:
        return None
    except (subprocess.SubprocessError, OSError):
        return None  # analyseur indisponible : on ne bloque rien
    return f"✓ Syntaxe vérifiée ({label}).", True
