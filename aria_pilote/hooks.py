"""Hooks : des commandes PowerShell déclenchées automatiquement autour du travail de l'agent.

Fichier hooks.json, global (dans l'appli) ou de projet (<dossier de travail>/.aria/hooks.json) :
    {
        "avant_outil": [{"outils": ["run_command"], "commande": "..."}],
        "apres_outil": [{"outils": ["write_file", "edit_file", "multi_edit"], "commande": "..."}],
        "debut_tache": [{"commande": "git status --short"}],
        "fin_tache":   [{"commande": "..."}]
    }
La commande reçoit le contexte en JSON sur l'entrée standard et dans des variables d'environnement
(ARIA_EVENEMENT, ARIA_OUTIL, ARIA_ARGS, ARIA_FICHIER, ARIA_STATUT). Pour « avant_outil », un code de
sortie 2 bloque l'action et sa sortie en donne la raison au modèle.

Les hooks d'un projet ne tournent qu'après approbation de leur contenu exact (empreinte SHA-256).
"""

import asyncio
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

from . import config, jobs, workspace
from .tools import POWERSHELL

EVENTS = ("avant_outil", "apres_outil", "debut_tache", "fin_tache")
GLOBAL_PATH = config.ROOT / "hooks.json"
PROJECT_REL = Path(".aria") / "hooks.json"
TIMEOUT_S = 30
MAX_OUTPUT_CHARS = 4000
BLOCK_CODE = 2


class HookError(Exception):
    pass


def validate(data):
    if not isinstance(data, dict):
        raise HookError("hooks.json doit contenir un objet JSON.")
    for event, rules in data.items():
        if event not in EVENTS:
            raise HookError(f"Événement inconnu : {event} (attendus : {', '.join(EVENTS)}).")
        if not isinstance(rules, list):
            raise HookError(f"« {event} » doit être une liste de règles.")
        for rule in rules:
            if not isinstance(rule, dict) or not isinstance(rule.get("commande"), str) or not rule["commande"].strip():
                raise HookError(f"Chaque règle de « {event} » doit avoir une « commande ».")
            if "outils" in rule and not (isinstance(rule["outils"], list) and all(isinstance(t, str) for t in rule["outils"])):
                raise HookError(f"« outils » doit être une liste de noms d'outils (règle de « {event} »).")
    return data


def _read(path):
    text = path.read_text(encoding="utf-8-sig")
    try:
        return text, validate(json.loads(text))
    except json.JSONDecodeError as exc:
        raise HookError(f"JSON invalide dans {path.name} : {exc}")


def _fingerprint(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _project_path(root):
    try:
        return workspace.validate_root(root) / PROJECT_REL
    except workspace.WorkspaceError:
        return None


def project_status(root):
    """Hooks du projet : contenu, approuvé ou non. None s'il n'y en a pas."""
    path = _project_path(root) if root else None
    if path is None or not path.is_file():
        return None
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    trusted = config.get().get("trusted_hooks", {}).get(str(path)) == _fingerprint(text)
    error = None
    try:
        _read(path)
    except HookError as exc:
        error = str(exc)
    return {"path": str(path), "content": text, "trusted": trusted, "error": error}


def trust(root):
    status = project_status(root)
    if status is None:
        raise HookError("Ce dossier n'a pas de hooks de projet.")
    if status["error"]:
        raise HookError(status["error"])
    config.update({"trusted_hooks": {status["path"]: _fingerprint(status["content"])}})


def read_global():
    return GLOBAL_PATH.read_text(encoding="utf-8-sig") if GLOBAL_PATH.exists() else ""


def save_global(text):
    if text.strip():
        try:
            validate(json.loads(text))
        except json.JSONDecodeError as exc:
            raise HookError(f"JSON invalide : {exc}")
        GLOBAL_PATH.write_text(text, encoding="utf-8")
    elif GLOBAL_PATH.exists():
        GLOBAL_PATH.unlink()


def _rules(event, root):
    """Règles actives : globales, puis celles du projet si elles sont approuvées."""
    sources = []
    if GLOBAL_PATH.exists():
        try:
            sources.append(("global", _read(GLOBAL_PATH)[1]))
        except (HookError, OSError):
            pass
    status = project_status(root) if root else None
    if status and status["trusted"] and not status["error"]:
        sources.append(("projet", _read(Path(status["path"]))[1]))
    return [(source, rule) for source, data in sources for rule in data.get(event, [])]


def _run_one(command, cwd, payload):
    env = {
        **os.environ,
        "ARIA_EVENEMENT": payload["evenement"],
        "ARIA_OUTIL": payload.get("outil") or "",
        "ARIA_ARGS": json.dumps(payload.get("arguments") or {}, ensure_ascii=False),
        "ARIA_FICHIER": str((payload.get("arguments") or {}).get("path", "")),
        "ARIA_STATUT": (payload.get("resultat") or {}).get("statut", ""),
    }
    script = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n$ProgressPreference = 'SilentlyContinue'\n" + command
    fd, path = tempfile.mkstemp(prefix="aria-hook-", suffix=".ps1")
    with os.fdopen(fd, "w", encoding="utf-8-sig") as f:
        f.write(script)
    args = [POWERSHELL, "-NoProfile", "-NonInteractive"]
    if POWERSHELL == "powershell.exe":
        args += ["-ExecutionPolicy", "Bypass"]
    try:
        proc = subprocess.Popen(
            args + ["-File", path], cwd=cwd, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        jobs.attach(proc)
        try:
            stdout, _ = proc.communicate(json.dumps(payload, ensure_ascii=False).encode("utf-8"), timeout=TIMEOUT_S)
            code = proc.returncode
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, _ = proc.communicate()
            code, stdout = -1, stdout + f"\n(hook arrêté après {TIMEOUT_S} s)".encode("utf-8")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    return code, stdout.decode("utf-8", errors="replace").strip()[:MAX_OUTPUT_CHARS]


async def run(event, root, tool=None, arguments=None, result=None):
    """Lance les hooks d'un événement ; renvoie [{source, commande, code, sortie}]."""
    outcomes = []
    for source, rule in _rules(event, root):
        targets = rule.get("outils") or ["*"]
        if tool and "*" not in targets and tool not in targets:
            continue
        payload = {"evenement": event, "outil": tool, "arguments": arguments, "dossier": root,
                   "resultat": {"statut": result["status"], "sortie": result["output"][:MAX_OUTPUT_CHARS]} if result else None}
        code, output = await asyncio.to_thread(_run_one, rule["commande"], root, payload)
        outcomes.append({"source": source, "commande": rule["commande"], "code": code, "sortie": output})
    return outcomes


def blocking(outcomes):
    """Premier hook « avant_outil » qui demande le blocage (code 2), ou None."""
    return next((o for o in outcomes if o["code"] == BLOCK_CODE), None)
