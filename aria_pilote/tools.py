"""Outils de l'agent.

Chaque outil a deux temps :
- prepare(root, args) valide les arguments et calcule l'aperçu (diff, commande) sans rien modifier ;
- run(prepared, cid) exécute l'action, une fois l'accord donné si l'outil en demande un.
"""

import asyncio
import difflib
import fnmatch
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from . import background, backups, checks, conversations, images, jobs, screen, skills, web, workspace
from .server import manager

SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".idea"}
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_GREP_FILE_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_CHARS = 20000
COMMAND_TIMEOUT_S = 120


class ToolError(Exception):
    pass


@dataclass
class Tool:
    name: str
    kind: str  # read | state | write | command | web | screen | screen_action
    description: str
    parameters: dict
    prepare: Callable[[str, dict], dict]
    run: Callable[[dict, str], Awaitable[dict]]
    # Facultatif : complète la préparation avec l'état de la conversation (cible d'une action à l'écran), avant l'accord
    bind: Callable[[dict, str], dict] | None = None


# --- utilitaires ---------------------------------------------------------------

def _truncate(text, limit=MAX_OUTPUT_CHARS):
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… (sortie tronquée : {len(text)} caractères au total)"


def _resolve(root, path):
    try:
        return workspace.resolve(root, path)
    except workspace.WorkspaceError as exc:
        raise ToolError(str(exc))


def _is_binary(path):
    with open(path, "rb") as f:
        return b"\0" in f.read(8192)


def _read_text(path):
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ToolError(f"Fichier trop gros ({path.stat().st_size // 1024} Ko, limite {MAX_FILE_BYTES // 1024 // 1024} Mo).")
    if _is_binary(path):
        raise ToolError("Fichier binaire : lecture refusée.")
    # newline="" conserve les fins de ligne d'origine (CRLF sous Windows)
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
        return f.read()


def _walk(root, base):
    """Parcourt `base` sans suivre les liens ou jonctions qui sortent du dossier de travail."""
    root_real = os.path.realpath(workspace.validate_root(root))

    def inside(path):
        real = os.path.realpath(path)
        return real == root_real or real.startswith(root_real + os.sep)

    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(
            d for d in dirnames if d not in SKIP_DIRS and inside(os.path.join(dirpath, d))
        )
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if inside(full):
                yield full


def _diff(old, new, name):
    lines = list(difflib.unified_diff(
        old.splitlines(), new.splitlines(), f"a/{name}", f"b/{name}", lineterm="", n=3
    ))
    if len(lines) > 400:
        lines = lines[:400] + [f"… diff tronqué ({len(lines)} lignes)"]
    return "\n".join(lines) or "(aucun changement)"


def _ok(output, **extra):
    return {"status": "ok", "output": output, **extra}


def _schema(properties, required):
    return {"type": "object", "properties": properties, "required": required}


# --- lecture -------------------------------------------------------------------

def prepare_list_dir(root, args):
    target = _resolve(root, args.get("path", "."))
    if not target.is_dir():
        raise ToolError("Ce n'est pas un dossier.")
    entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    lines = []
    for p in entries[:300]:
        if p.is_dir():
            lines.append(f"{p.name}/")
        else:
            lines.append(f"{p.name}  ({p.stat().st_size / 1024:.1f} Ko)")
    if len(entries) > 300:
        lines.append(f"… et {len(entries) - 300} autres éléments")
    return {"output": "\n".join(lines) or "(dossier vide)"}


def prepare_read_file(root, args):
    target = _resolve(root, args.get("path", ""))
    if not target.is_file():
        raise ToolError("Fichier introuvable.")
    lines = _read_text(target).splitlines()
    start = max(1, int(args.get("start_line") or 1))
    count = min(1000, max(1, int(args.get("max_lines") or 400)))
    chunk = lines[start - 1 : start - 1 + count]
    end = start + len(chunk) - 1
    body = "\n".join(f"{n:>5}│ {line}" for n, line in enumerate(chunk, start))
    header = f"{workspace.relative(root, target)} — lignes {start} à {end} sur {len(lines)}"
    return {"output": _truncate(f"{header}\n{body}")}


def prepare_find_files(root, args):
    pattern = args.get("pattern") or "*"
    base = _resolve(root, args.get("path", "."))
    matches = []
    for full in _walk(root, base):
        rel = workspace.relative(root, full)
        if fnmatch.fnmatch(os.path.basename(full), pattern) or fnmatch.fnmatch(rel, pattern):
            matches.append(rel)
            if len(matches) >= 200:
                matches.append("… limite de 200 résultats atteinte")
                break
    return {"output": "\n".join(matches) or "Aucun fichier trouvé."}


def prepare_grep(root, args):
    try:
        regex = re.compile(args.get("pattern", ""))
    except re.error as exc:
        raise ToolError(f"Expression régulière invalide : {exc}")
    base = _resolve(root, args.get("path", "."))
    file_pattern = args.get("file_pattern") or "*"
    files = [str(base)] if base.is_file() else _walk(root, base)
    hits = []
    for full in files:
        if not fnmatch.fnmatch(os.path.basename(full), file_pattern):
            continue
        try:
            if os.path.getsize(full) > MAX_GREP_FILE_BYTES or _is_binary(full):
                continue
            with open(full, encoding="utf-8", errors="replace") as f:
                for n, line in enumerate(f, 1):
                    if regex.search(line):
                        hits.append(f"{workspace.relative(root, full)}:{n}: {line.rstrip()[:200]}")
                        if len(hits) >= 200:
                            break
        except OSError:
            continue
        if len(hits) >= 200:
            hits.append("… limite de 200 résultats atteinte")
            break
    return {"output": "\n".join(hits) or "Aucune correspondance."}


async def run_prepared_output(prepared, cid):
    return _ok(prepared["output"])


# --- écriture --------------------------------------------------------------------

def prepare_write_file(root, args):
    target = _resolve(root, args.get("path", ""))
    if target.is_dir():
        raise ToolError("C'est un dossier, pas un fichier.")
    content = args.get("content")
    if not isinstance(content, str):
        raise ToolError("Le paramètre « content » est obligatoire.")
    old = _read_text(target) if target.exists() else ""
    rel = workspace.relative(root, target)
    return {"target": target, "content": content, "rel": rel, "preview": _diff(old, content, rel), "before": old if target.exists() else None}


MAX_EDITS = 20


def _replace(text, edit):
    """Applique un remplacement {old_string, new_string, replace_all} ; renvoie le texte modifié."""
    old_string, new_string = edit.get("old_string"), edit.get("new_string")
    if not isinstance(old_string, str) or not old_string or not isinstance(new_string, str):
        raise ToolError("« old_string » (non vide) et « new_string » sont obligatoires.")
    count = text.count(old_string)
    if count == 0 and "\r\n" in text:
        # Le modèle écrit des \n alors que le fichier est en CRLF
        old_string, new_string = old_string.replace("\n", "\r\n"), new_string.replace("\n", "\r\n")
        count = text.count(old_string)
    if count == 0:
        raise ToolError("Passage introuvable : relis le fichier et copie le texte exact.")
    if edit.get("replace_all"):
        return text.replace(old_string, new_string)
    if count > 1:
        raise ToolError(f"Le passage apparaît {count} fois : ajoute du contexte pour qu'il soit unique, "
                        "ou mets replace_all pour tous les remplacer.")
    return text.replace(old_string, new_string, 1)


def _prepare_edits(root, path, edits):
    target = _resolve(root, path)
    if not target.is_file():
        raise ToolError("Fichier introuvable.")
    text = content = _read_text(target)
    for number, edit in enumerate(edits, 1):
        try:
            content = _replace(content, edit if isinstance(edit, dict) else {})
        except ToolError as exc:
            # Tout ou rien : le fichier n'est jamais laissé à moitié modifié
            prefix = f"Remplacement n° {number} : " if len(edits) > 1 else ""
            suffix = " Aucun remplacement n'a été appliqué." if len(edits) > 1 else ""
            raise ToolError(f"{prefix}{exc}{suffix}")
    rel = workspace.relative(root, target)
    return {"target": target, "content": content, "rel": rel, "preview": _diff(text, content, rel), "before": text}


def prepare_edit_file(root, args):
    return _prepare_edits(root, args.get("path", ""), [args])


def prepare_multi_edit(root, args):
    edits = args.get("edits")
    if not isinstance(edits, list) or not edits:
        raise ToolError("« edits » doit être une liste de remplacements {old_string, new_string}.")
    if len(edits) > MAX_EDITS:
        raise ToolError(f"{len(edits)} remplacements : {MAX_EDITS} au plus par appel. Au-delà, réécris le fichier "
                        "avec write_file.")
    return _prepare_edits(root, args.get("path", ""), edits)


async def run_write(prepared, cid):
    def write():
        target = prepared["target"]
        # Le fichier a pu changer pendant que l'utilisateur lisait le diff
        current = _read_text(target) if target.exists() else None
        if current != prepared["before"]:
            raise ToolError("Le fichier a changé depuis l'aperçu : relis-le avant de réessayer.")
        backup_id = backups.save(cid, target)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="") as f:
            f.write(prepared["content"])
        backups.record_after(cid, backup_id, target)
        return backup_id

    backup_id = await asyncio.to_thread(write)
    lines = len(prepared["content"].splitlines())
    output = f"Fichier enregistré : {prepared['rel']} ({lines} lignes)."
    # Vérification automatique de la syntaxe : informe le modèle, ne bloque rien
    verdict = await asyncio.to_thread(checks.check, prepared["target"])
    if verdict is None:
        return _ok(output, backup_id=backup_id)
    note, valid = verdict
    return _ok(f"{output}\n{note}", backup_id=backup_id, syntax=valid)


# --- commandes -------------------------------------------------------------------

# Filet de sécurité en plus de ton accord : ces commandes sont refusées d'office, avant
# même de te les montrer. Ce n'est pas une frontière infranchissable (PowerShell offre
# mille détours), juste une protection contre un « Autoriser » cliqué trop vite.
_RM = r"\b(?:remove-item|rm|ri|del|erase|rd|rmdir)\b"
DENYLIST = [(re.compile(p, re.IGNORECASE), reason) for p, reason in [
    (r"\bformat-volume\b|\bformat(?:\.com)?\s+[a-z]:", "formatage d'un disque"),
    (r"\b(?:diskpart|clear-disk|initialize-disk|remove-partition|set-disk)\b", "manipulation des disques et partitions"),
    (r"\bbcd(?:edit|boot)\b", "configuration du démarrage de Windows"),
    (r"\b(?:vssadmin|wbadmin)\b.*\bdelete\b|\bshadowcopy\b.*\bdelete\b", "suppression des sauvegardes système"),
    (r"\breg(?:\.exe)?\s+delete\s+(?:hklm|hkey_local_machine)\b|\bremove-item(?:property)?\b.*\bhklm:", "suppression dans le registre système"),
    (_RM + r"[^;|\n]*?(?<![\w.\\])[a-z]:\\?(?=[\s\"';|]|$)", "suppression à la racine d'un disque"),
    (_RM + r"[^;|\n]*(?:\\windows\b|\\program files|\$env:(?:systemroot|windir|programfiles))", "suppression de fichiers système"),
    (r"\b(?:stop-computer|restart-computer)\b|\bshutdown(?:\.exe)?\s+[/-][srp]\b", "arrêt ou redémarrage de la machine"),
    (r"\bcipher(?:\.exe)?\s+/w\b|\bsdelete\b", "effacement définitif de données"),
    (r"\bset-mppreference\b|\badd-mppreference\b.*exclusion|\bnetsh\b.*\bstate\s+off\b", "désactivation de l'antivirus ou du pare-feu"),
    (r"\b(?:iex|invoke-expression)\b.*\b(?:iwr|irm|invoke-webrequest|invoke-restmethod|downloadstring|net\.webclient)\b"
     r"|\b(?:iwr|irm|invoke-webrequest|invoke-restmethod)\b.*\|\s*(?:iex|invoke-expression)\b", "téléchargement puis exécution de code"),
    (r"\b(?:powershell|pwsh)(?:\.exe)?\b.*\s-(?:e|ec|enc|encodedcommand)\s+\S{16,}", "commande encodée, impossible à relire"),
    (r"\b(?:takeown|icacls)\b.*\b[a-z]:\\(?:windows|program files)?\\?(?=[\s\"]|$)", "prise de possession de fichiers système"),
    (r"\bdd\s+if=|\bmkfs\b", "écriture brute sur un disque"),
]]


def check_denylist(command):
    """Raison du refus si la commande est sur la liste noire, sinon None."""
    for pattern, reason in DENYLIST:
        if pattern.search(command):
            return reason
    return None


def prepare_run_command(root, args):
    command = (args.get("command") or "").strip()
    if not command:
        raise ToolError("Commande vide.")
    reason = check_denylist(command)
    if reason:
        raise ToolError(f"Commande refusée d'office ({reason}). Elle est sur la liste noire d'Aria Pilote : "
                        "propose une autre approche, ou demande à l'utilisateur de la lancer lui-même.")
    background_mode = bool(args.get("background"))
    keep = background_mode and bool(args.get("keep"))
    preview = command
    if background_mode:
        preview += "\n\n# ⚙ en arrière-plan" + (" (gardée après la tâche, à ta demande)" if keep else
                                               " (arrêtée à la fin de la tâche)")
    return {"command": command, "cwd": str(workspace.validate_root(root)), "preview": preview,
            "background": background_mode, "keep": keep}


# PowerShell 7 si présent (gère && et ||), sinon Windows PowerShell 5.1
POWERSHELL = shutil.which("pwsh") or "powershell.exe"
POWERSHELL_NAME = "PowerShell 7" if POWERSHELL != "powershell.exe" else "Windows PowerShell 5.1 (pas de && ni ||, utilise ;)"

# Les erreurs sont converties en texte lisible : sinon PowerShell les renvoie en XML (« #< CLIXML »)
# quand sa sortie est redirigée. Le code de sortie vaut 1 si une erreur PowerShell a eu lieu ;
# le stderr d'un programme externe (git, pip…) est affiché tel quel sans compter comme une erreur.
COMMAND_WRAPPER = """[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ProgressPreference = 'SilentlyContinue'
$script:erreurs = 0
& {{
{command}
}} 2>&1 | ForEach-Object {{
    if ($_ -is [System.Management.Automation.ErrorRecord]) {{
        if ($_.Exception -is [System.Management.Automation.RemoteException]) {{ $_.Exception.Message }}
        else {{ $script:erreurs++; "ERREUR : $($_.Exception.Message)" }}
    }} else {{ $_ }}
}} | Out-String -Stream -Width 250
if ($LASTEXITCODE) {{ exit $LASTEXITCODE }} elseif ($script:erreurs) {{ exit 1 }}
"""


def _powershell_args(script_path):
    # Script temporaire lancé avec -File : un « -EncodedCommand » en base64 ressemble trop aux
    # attaques que Defender surveille. PowerShell 7 accepte un script local (RemoteSigned par
    # défaut) ; seul Windows PowerShell 5.1, en « Restricted » par défaut, a besoin de Bypass.
    args = [POWERSHELL, "-NoProfile", "-NonInteractive"]
    if POWERSHELL == "powershell.exe":
        args += ["-ExecutionPolicy", "Bypass"]
    return args + ["-File", script_path]


async def run_command(prepared, cid):
    script = COMMAND_WRAPPER.format(command=prepared["command"])
    fd, script_path = tempfile.mkstemp(prefix="aria-commande-", suffix=".ps1")
    # UTF-8 avec BOM : seul encodage que les deux versions de PowerShell lisent sans abîmer les accents
    with os.fdopen(fd, "w", encoding="utf-8-sig") as f:
        f.write(script)
    if prepared.get("background"):
        return await _run_background(prepared, cid, script_path)
    try:
        proc = subprocess.Popen(
            _powershell_args(script_path),
            cwd=prepared["cwd"],
            env={**os.environ, **jobs.COMMAND_ENV},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        jobs.attach(proc)
        try:
            stdout, _ = await asyncio.to_thread(proc.communicate, timeout=COMMAND_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            jobs.kill_tree(proc.pid)
            stdout, _ = await asyncio.to_thread(proc.communicate)
            text = stdout.decode("utf-8", errors="replace")
            return {"status": "error", "output": _truncate(f"Délai de {COMMAND_TIMEOUT_S} s dépassé : commande arrêtée.\n{text}")}
        except asyncio.CancelledError:
            # Bouton Stop : on ne laisse pas tourner la commande en arrière-plan
            jobs.kill_tree(proc.pid)
            raise
        text = stdout.decode("utf-8", errors="replace")
        status = "ok" if proc.returncode == 0 else "error"
        return {"status": status, "output": _truncate(f"Code de sortie : {proc.returncode}\n{text}")}
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass


# --- commandes en arrière-plan -----------------------------------------------------

def _unlink_quietly(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def _bg_report(cmd, fresh, lost=0, note=""):
    lines = [f"{cmd.id} : {cmd.state()}."]
    if note:
        lines.append(note)
    if lost:
        lines.append(f"({lost} caractères plus anciens n'ont pas été gardés.)")
    lines.append(fresh.rstrip() or "(aucune nouvelle sortie)")
    return _truncate("\n".join(lines))


async def _run_background(prepared, cid, script_path):
    try:
        cmd = background.start(cid, backups.running_task(cid), prepared["command"], _powershell_args(script_path),
                               prepared["cwd"], keep=prepared["keep"], cleanup=lambda: _unlink_quietly(script_path))
    except background.BackgroundError as exc:
        _unlink_quietly(script_path)
        raise ToolError(str(exc))
    # Quelques secondes pour attraper un plantage immédiat (port déjà pris, fichier introuvable…)
    await asyncio.to_thread(background.wait_exit, cmd, background.START_WAIT_S)
    fresh, _, lost = background.read(cmd)
    if cmd.running:
        note = (f"Lancée en arrière-plan : lis sa sortie avec command_output (id « {cmd.id} », wait_for pour attendre "
                "un message précis) et arrête-la avec stop_command quand tu n'en as plus besoin.")
        return {"status": "ok", "output": _bg_report(cmd, fresh, lost, note), "background_id": cmd.id}
    return {"status": "ok" if cmd.returncode == 0 else "error", "output": _bg_report(cmd, fresh, lost),
            "background_id": cmd.id}


def prepare_command_output(root, args):
    command_id = str(args.get("id") or "").strip()
    if not command_id:
        raise ToolError("Le paramètre « id » est obligatoire (ex. bg1).")
    wait_for = args.get("wait_for")
    wait_for = wait_for.strip() if isinstance(wait_for, str) and wait_for.strip() else None
    try:
        timeout = float(args.get("timeout", 15 if wait_for else 0))
    except (TypeError, ValueError):
        raise ToolError("« timeout » doit être un nombre de secondes.")
    return {"id": command_id, "wait_for": wait_for, "timeout": timeout, "preview": command_id}


async def run_command_output(prepared, cid):
    try:
        cmd = background.get(cid, prepared["id"])
    except background.BackgroundError as exc:
        raise ToolError(str(exc))
    fresh, found, lost = await asyncio.to_thread(background.read, cmd, prepared["wait_for"], prepared["timeout"])
    note = ""
    if prepared["wait_for"] and not found:
        waited = min(prepared["timeout"], background.MAX_WAIT_S)
        note = f"« {prepared['wait_for']} » n'est pas apparu" + ("." if not cmd.running else f" en {waited:.0f} s.")
    return {"status": "ok", "output": _bg_report(cmd, fresh, lost, note)}


def prepare_stop_command(root, args):
    command_id = str(args.get("id") or "").strip()
    if not command_id:
        raise ToolError("Le paramètre « id » est obligatoire (ex. bg1).")
    return {"id": command_id, "preview": command_id}


async def run_stop_command(prepared, cid):
    try:
        cmd = background.get(cid, prepared["id"])
    except background.BackgroundError as exc:
        raise ToolError(str(exc))
    was_running = cmd.running
    await asyncio.to_thread(background.stop, cmd)
    fresh, _, lost = background.read(cmd)
    note = "Arrêtée." if was_running else "Elle était déjà terminée."
    return {"status": "ok", "output": _bg_report(cmd, fresh, lost, note)}


# --- plan de l'agent ----------------------------------------------------------------

TODO_MARKS = {"pending": "☐", "in_progress": "▶", "completed": "☑"}


def prepare_update_todo(root, args):
    items = args.get("items")
    if not isinstance(items, list) or not items:
        raise ToolError("« items » doit être une liste non vide.")
    clean = []
    for it in items[:30]:
        if isinstance(it, dict):
            content, status = str(it.get("content", "")).strip(), it.get("status", "pending")
        else:
            content, status = str(it).strip(), "pending"
        if content:
            clean.append({"content": content, "status": status if status in TODO_MARKS else "pending"})
    if not clean:
        raise ToolError("Aucune étape valide dans « items ».")
    return {"items": clean}


async def run_update_todo(prepared, cid):
    items = prepared["items"]

    def store(conv):
        # Le plan appartient à la tâche en cours : l'interface ne l'affiche sous la réponse que pour cette tâche
        conv["todo"] = {"task_id": conv.get("task_id"), "items": items}

    conversations.update(cid, store)
    text = "\n".join(f"{TODO_MARKS[i['status']]} {i['content']}" for i in items)
    return _ok(f"Plan mis à jour :\n{text}", todo=items)


# --- compétences -----------------------------------------------------------------

def prepare_load_skill(root, args):
    try:
        skill = skills.get((args.get("name") or "").strip(), root)
        if args.get("file"):
            return {"output": _truncate(skills.read_extra(skill, args["file"]))}
        text = skills.body(skill)
    except skills.SkillError as exc:
        names = ", ".join(skills.discover(root)) or "aucune"
        raise ToolError(f"{exc} Compétences disponibles : {names}.")
    files = skills.extra_files(skill)
    if files:
        text += "\n\nFichiers de la compétence (à lire avec load_skill et le paramètre file) : " + ", ".join(files)
    return {"output": _truncate(f"Compétence « {skill.name} » ({skill.source}) :\n\n{text}")}


# --- web -------------------------------------------------------------------------

# --- écran (chantier 4) ---------------------------------------------------------------------------
# Observer : lire_fenetre (texte) et voir_ecran (capture). Agir : agir_ecran, toujours sur la dernière fenêtre
# observée, avec ton accord à chaque action, puis une nouvelle observation renvoyée au modèle.

SCREEN_ACTIONS = ("cliquer", "double_clic", "clic_droit", "taper", "touches", "defiler", "glisser", "restaurer")
MAX_ZOOM = 3  # une petite zone est agrandie jusqu'à 3 fois pour que le texte se lise
SCROLL_DIRECTIONS = ("haut", "bas", "gauche", "droite")


def prepare_screen(root, args):
    zone = args.get("zone")
    if zone is not None:
        if not isinstance(zone, list) or len(zone) != 4:
            raise ToolError("« zone » : quatre nombres [x0, y0, x1, y1], pris sur la dernière capture.")
        zone = [_number(v, "zone") for v in zone]
        if zone[2] - zone[0] < 8 or zone[3] - zone[1] < 8:
            raise ToolError("« zone » trop petite : au moins 8 pixels de côté sur la dernière capture.")
    return {"fenetre": str(args.get("fenetre") or "").strip(), "zone": zone}


def _screen_allow(cid):
    """Programmes que l'utilisateur a autorisés pour cette conversation (« Prendre la main »), ou None = tous."""
    allow = (conversations.get(cid) or {}).get("screen_allow")
    return allow if isinstance(allow, list) else None


MAX_SCREEN_ACTIONS = 40  # actions à l'écran au plus par tâche : au-delà, l'agent s'arrête et rend compte


def _screen_action_count(cid):
    conv = conversations.get(cid) or {}
    return sum(1 for m in conv.get("messages", [])
               if m.get("name") == "agir_ecran" and m.get("status") == "ok" and m.get("task_id") == conv.get("task_id"))


def _remember_screen(cid, window, elements, capture=None, zone=None):
    """Ce que le modèle a vu en dernier : agir_ecran ne vise que cette fenêtre (et ce qui est ouvert par-dessus : la
    `zone`), ses numéros et sa capture."""
    state = {"window": window["id"] if window else None, "title": window["titre"] if window else None,
             "rect": list(window["rect"]) if window else None,
             "zone": list(zone) if zone else (list(window["rect"]) if window else None),
             "elements": {str(e["n"]): {k: e[k] for k in ("type", "nom", "rect", "mot_de_passe")} for e in elements},
             "capture": capture}
    conversations.update(cid, lambda conv: conv.__setitem__("screen_state", state))


def _popups(window):
    """Menus et boîtes de dialogue ouverts par-dessus la fenêtre (même appli) ; aucun si Windows ne répond pas."""
    try:
        return screen.popups(window)
    except screen.ScreenError:
        return []


def _observe(cid, window, capture):
    """Éléments (et capture si demandée) d'une fenêtre et de ce qui est ouvert par-dessus : texte pour le modèle,
    images à joindre, état retenu."""
    extra = _popups(window)
    if extra:
        elements, texts, truncated = screen.read_elements(window, extra=extra)
        text = screen.describe_elements(window, elements, texts, truncated, extra)
    else:
        elements, texts, truncated = screen.read_elements(window)
        text = screen.describe_elements(window, elements, texts, truncated)
    area = screen.zone(window, extra)
    if not capture:
        _remember_screen(cid, window, elements, zone=area)
        return text, []
    if extra:
        image, origin = screen.capture_region(area), area[:2]  # un menu n'apparaît pas dans la capture de la fenêtre seule
    else:
        image, origin = screen.capture_window(window)
    small, scale = screen.fit(image, images.MAX_SIDE)
    if elements:
        small = screen.draw_marks(small, elements, origin, scale)
    _remember_screen(cid, window, elements, {"origin": list(origin), "scale": scale, "size": list(small.size)}, area)
    if not manager.vision:
        return text + "\n\nLe modèle chargé ne voit pas les images : seule la liste ci-dessus est disponible.", []
    note = "les cadres numérotés correspondent à la liste" if elements else "image réduite"
    return text + f"\n\nLa capture est jointe juste après ce résultat ({note}).", [images.store(cid, small, "png")]


def _read_window(spec, cid):
    try:
        listed = screen.allowed_windows(screen.windows(), _screen_allow(cid))
        if not spec:
            return _ok(screen.describe_windows(listed))
        window = screen.find(spec, listed)
        screen.ensure_allowed(window, _screen_allow(cid))
        text, _ = _observe(cid, window, capture=False)
    except screen.ScreenError as exc:
        raise ToolError(str(exc))
    return _ok(_truncate(text))


def _zoom(zone, cid):
    """Une zone de la dernière capture, recapturée en pleine résolution et agrandie si elle est petite : pour lire un
    petit texte. Les numéros d'éléments restent valables, et les x, y se lisent désormais sur ce zoom."""
    state = (conversations.get(cid) or {}).get("screen_state") or {}
    capture = state.get("capture")
    if not capture:
        raise ToolError("Le zoom part d'une capture : appelle d'abord voir_ecran sur la fenêtre.")
    ox, oy, scale = capture["origin"][0], capture["origin"][1], capture["scale"]
    rect = [ox + zone[0] / scale, oy + zone[1] / scale, ox + zone[2] / scale, oy + zone[3] / scale]
    bounds = state.get("zone")
    if bounds:  # jamais au-delà de ce qui a été observé
        rect = [max(rect[0], bounds[0]), max(rect[1], bounds[1]), min(rect[2], bounds[2]), min(rect[3], bounds[3])]
    if rect[2] - rect[0] < 4 or rect[3] - rect[1] < 4:
        raise ToolError("Cette zone tombe hors de ce qui a été observé.")
    try:
        image = screen.capture_region(rect)
    except screen.ScreenError as exc:
        raise ToolError(str(exc))
    factor = min(MAX_ZOOM, images.MAX_SIDE / max(image.size))
    if factor > 1:
        image = image.resize((round(image.width * factor), round(image.height * factor)))
    else:
        image, factor = screen.fit(image, images.MAX_SIDE)
    inside = [{**e, "n": int(n), "rect": tuple(e["rect"])} for n, e in state.get("elements", {}).items()
              if e["rect"][0] < rect[2] and e["rect"][2] > rect[0] and e["rect"][1] < rect[3] and e["rect"][3] > rect[1]]
    if inside:
        image = screen.draw_marks(image, inside, rect[:2], factor)
    new_capture = {"origin": rect[:2], "scale": factor, "size": list(image.size)}
    conversations.update(cid, lambda conv: conv.setdefault("screen_state", {}).__setitem__("capture", new_capture))
    text = (f"Zoom sur la zone ({zone[0]:.0f}, {zone[1]:.0f}) – ({zone[2]:.0f}, {zone[3]:.0f}) de la capture précédente, "
            f"agrandie ×{factor:.1f}. Les x, y d'agir_ecran se lisent maintenant sur ce zoom ; les numéros restent valables.")
    if not manager.vision:
        return _ok(text + "\n\nLe modèle chargé ne voit pas les images : le zoom ne lui sert à rien.")
    return _ok(text + "\n\nLe zoom est joint juste après ce résultat.", images=[images.store(cid, image, "png")])


def _see_screen(spec, cid, zone=None):
    if zone:
        return _zoom(zone, cid)
    try:
        listed = screen.allowed_windows(screen.windows(), _screen_allow(cid))
        if spec:
            window = screen.find(spec, listed)
            screen.ensure_allowed(window, _screen_allow(cid))
            text, shots = _observe(cid, window, capture=True)
        elif _screen_allow(cid) is not None:
            raise ToolError("Tu m'as limité à certaines fenêtres : donne le nom d'une de ces fenêtres à voir_ecran, "
                            "je ne capture pas tout l'écran dans ce cas.")
        else:
            image, origin = screen.capture_screen()
            small, scale = screen.fit(image, images.MAX_SIDE)
            _remember_screen(cid, None, [], {"origin": list(origin), "scale": scale, "size": list(small.size)})
            text = f"Capture de tous les écrans ({image.width}x{image.height} pixels).\n" + screen.describe_windows(listed)
            if manager.vision:
                text += "\n\nLa capture est jointe juste après ce résultat (image réduite)."
                shots = [images.store(cid, small, "png")]
            else:
                text += "\n\nLe modèle chargé ne voit pas les images : seule la liste ci-dessus est disponible."
                shots = []
    except screen.ScreenError as exc:
        raise ToolError(str(exc))
    return _ok(_truncate(text), **({"images": shots} if shots else {}))


async def run_read_window(prepared, cid):
    return await asyncio.to_thread(_read_window, prepared["fenetre"], cid)


async def run_see_screen(prepared, cid):
    return await asyncio.to_thread(_see_screen, prepared["fenetre"], cid, prepared.get("zone"))


def _number(value, name):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ToolError(f"« {name} » doit être un nombre.")


def prepare_screen_action(root, args):
    """Vérifie la forme de la demande ; la cible est résolue ensuite, sur la dernière observation (bind)."""
    action = str(args.get("action") or "").strip().lower().replace("-", "_").replace(" ", "_")
    if action not in SCREEN_ACTIONS:
        raise ToolError(f"Action inconnue : « {action} ». Actions possibles : {', '.join(SCREEN_ACTIONS)}.")
    p = {"action": action, "element": _number(args.get("element"), "element"),
         "x": _number(args.get("x"), "x"), "y": _number(args.get("y"), "y"),
         "vers_element": _number(args.get("vers_element"), "vers_element"),
         "vers_x": _number(args.get("vers_x"), "vers_x"), "vers_y": _number(args.get("vers_y"), "vers_y"),
         "texte": args.get("texte"), "remplacer": bool(args.get("remplacer")),
         "touches": str(args.get("touches") or "").strip(),
         "direction": str(args.get("direction") or "bas").strip().lower(),
         "quantite": int(_number(args.get("quantite"), "quantite") or 3),
         "attente": min(screen.MAX_WAIT_S, max(0.0, _number(args.get("attente"), "attente") or 0.0))}
    pointed = p["element"] is not None or (p["x"] is not None and p["y"] is not None)
    if action in ("cliquer", "double_clic", "clic_droit", "glisser") and not pointed:
        raise ToolError("Indique la cible : « element » (son numéro) ou « x » et « y » (sur la dernière capture).")
    if action == "glisser" and p["vers_element"] is None and (p["vers_x"] is None or p["vers_y"] is None):
        raise ToolError("Indique où lâcher : « vers_element », ou « vers_x » et « vers_y ».")
    p["fenetre"] = str(args.get("fenetre") or "").strip()
    if action == "restaurer" and not p["fenetre"]:
        raise ToolError("Indique la « fenetre » à rouvrir (id, morceau du titre ou programme).")
    if action == "taper" and not (isinstance(p["texte"], str) and p["texte"]):
        raise ToolError("Indique le « texte » à taper.")
    if action == "touches":
        try:
            screen.parse_keys(p["touches"])
        except screen.ScreenError as exc:
            raise ToolError(str(exc))
    if action == "defiler":
        if p["direction"] not in SCROLL_DIRECTIONS:
            raise ToolError(f"« direction » : {', '.join(SCROLL_DIRECTIONS)}.")
        p["quantite"] = min(20, max(1, p["quantite"]))
    return p


def _target_window(state, _allow=None):
    """La fenêtre observée en dernier, telle qu'elle est maintenant : refus si elle a changé ou n'est pas autorisée."""
    if not state or not state.get("window"):
        raise ToolError("Observe d'abord la fenêtre où agir (lire_fenetre ou voir_ecran avec son nom) : "
                        "je n'agis que sur ce que tu as vu.")
    try:
        window = screen.find(str(state["window"]))
    except screen.ScreenError:
        raise ToolError(f"La fenêtre « {state['title']} » n'existe plus : observe à nouveau.")
    try:
        screen.ensure_allowed(window, _allow)  # interdite d'office, ou hors de la liste autorisée
    except screen.ScreenError as exc:
        raise ToolError(str(exc))
    if window["reduite"]:
        raise ToolError(f"La fenêtre « {window['titre']} » est réduite : rouvre-la avec agir_ecran, action « restaurer ».")
    if list(window["rect"]) != list(state["rect"]):
        raise ToolError("La fenêtre a bougé ou changé de taille depuis ta dernière observation : relis-la avant d'agir.")
    return window


def _point(state, window, element, x, y):
    """Point de l'écran visé : centre d'un élément numéroté, ou point de la dernière capture remis à l'échelle."""
    if element is not None:
        found = state["elements"].get(str(int(element)))
        if found is None:
            raise ToolError(f"Pas d'élément n° {int(element)} dans ta dernière observation de « {window['titre']} ».")
        left, top, right, bottom = found["rect"]
        return (left + right) / 2, (top + bottom) / 2, found
    capture = state.get("capture")
    if not capture:
        raise ToolError("Des coordonnées se lisent sur une capture : appelle d'abord voir_ecran sur cette fenêtre, "
                        "ou désigne un élément par son numéro.")
    sx, sy = capture["origin"][0] + x / capture["scale"], capture["origin"][1] + y / capture["scale"]
    left, top, right, bottom = state.get("zone") or window["rect"]  # la fenêtre et ce qui est ouvert par-dessus
    if not (left <= sx < right and top <= sy < bottom):
        raise ToolError(f"Le point ({x:.0f}, {y:.0f}) tombe hors de la fenêtre « {window['titre']} ».")
    return sx, sy, None


def _label(found, x, y):
    if found:
        return f"[{found.get('n', '')}] {screen.TYPES.get(found['type'], 'élément')} « {found['nom'] or 'sans nom'} »"
    return f"le point ({x:.0f}, {y:.0f}) de la capture"


SENSITIVE_WORDS = ("envoyer", "send", "envoi", "payer", "pay", "paiement", "acheter", "buy", "commander", "order",
                   "supprimer", "delete", "effacer", "remove", "publier", "publish", "poster", "confirmer", "confirm",
                   "valider", "soumettre", "submit", "transf", "virement", "s'abonner", "subscribe")


def _is_sensitive(name):
    low = " ".join(str(name or "").split()).lower()
    return any(word in low for word in SENSITIVE_WORDS)


def _last_screen_preview(cid):
    """Première ligne de l'aperçu de la dernière action à l'écran de la tâche (pour repérer une action répétée)."""
    conv = conversations.get(cid) or {}
    for m in reversed(conv.get("messages", [])):
        if m.get("name") == "agir_ecran" and m.get("task_id") == conv.get("task_id") and m.get("status") == "ok":
            return (m.get("content") or "").removeprefix("Fait : ").split(".", 1)[0]
    return None


def bind_screen_action(prepared, cid):
    """Cible résolue sur la dernière observation de la conversation, et l'aperçu montré dans la demande d'accord."""
    if _screen_action_count(cid) >= MAX_SCREEN_ACTIONS:
        raise ToolError(f"J'ai déjà fait {MAX_SCREEN_ACTIONS} actions à l'écran dans cette tâche : je m'arrête et je "
                        "te rends compte. Relance-moi si tu veux que je continue.")
    if prepared["action"] == "restaurer":
        return _bind_restore(prepared, cid)
    state = (conversations.get(cid) or {}).get("screen_state")
    window = _target_window(state, _screen_allow(cid))
    p = dict(prepared)
    action = p["action"]
    found = to_found = None
    if p["element"] is not None or p["x"] is not None:
        sx, sy, found = _point(state, window, p["element"], p["x"], p["y"])
        if found:
            found = {**found, "n": int(p["element"])}
        p["point"] = (sx, sy)
    if action == "glisser":
        tx, ty, to_found = _point(state, window, p["vers_element"], p["vers_x"], p["vers_y"])
        p["to"] = (tx, ty)
    if action == "taper" and found and found.get("mot_de_passe"):
        raise ToolError("C'est un champ mot de passe : c'est à l'utilisateur de le remplir lui-même.")
    target = _label(found, p["x"] or 0, p["y"] or 0) if "point" in p else None
    text = (p["texte"] or "")
    shown = text if len(text) <= 200 else text[:200] + "…"
    previews = {
        "cliquer": f"Cliquer sur {target}",
        "double_clic": f"Double-cliquer sur {target}",
        "clic_droit": f"Clic droit sur {target}",
        "taper": (f"Remplacer le contenu par « {shown} »" if p["remplacer"] else f"Taper « {shown} »")
                 + (f" dans {target}" if target else " là où est le curseur"),
        "touches": f"Appuyer sur {p['touches']}",
        "defiler": f"Faire défiler vers le {p['direction']} ({p['quantite']} crans)" + (f" sur {target}" if target else ""),
        "glisser": f"Glisser de {target} vers " + (_label({**to_found, 'n': int(p['vers_element'])}, 0, 0) if action == "glisser" and to_found
                                                   else f"le point ({p['vers_x'] or 0:.0f}, {p['vers_y'] or 0:.0f})"),
    }
    p["window"] = window
    first = previews[action]
    warnings = []
    # Bouton sensible : une action difficile à défaire (envoi, paiement, suppression, publication…)
    if action in ("cliquer", "double_clic") and found and _is_sensitive(found.get("nom")):
        warnings.append("⚠ Ce bouton déclenche une action difficile à défaire (envoi, paiement, suppression…).")
    # Action identique à la précédente : signe que l'agent insiste alors que rien ne bouge
    if first == _last_screen_preview(cid):
        warnings.append("⚠ Même action que la précédente : la fenêtre n'a peut-être pas réagi comme prévu.")
    p["preview"] = "\n".join([first, *warnings, f"Fenêtre : « {window['titre']} » ({window['processus']})"])
    return p


def _bind_restore(prepared, cid):
    """Rouvrir une fenêtre réduite (ou la remettre au premier plan) : une fenêtre autorisée, désignée par son nom."""
    allow = _screen_allow(cid)
    try:
        window = screen.find(prepared["fenetre"], screen.allowed_windows(screen.windows(), allow))
        screen.ensure_allowed(window, allow)
    except screen.ScreenError as exc:
        raise ToolError(str(exc))
    what = "Rouvrir" if window["reduite"] else "Mettre au premier plan"
    return {**prepared, "window": window,
            "preview": f"{what} la fenêtre « {window['titre']} »\nFenêtre : « {window['titre']} » ({window['processus']})"}


def _restore(p, cid):
    window = p["window"]
    screen.restore(window["id"])
    time.sleep(screen.SETTLE_S + p["attente"])
    still = next((w for w in screen.windows() if w["id"] == window["id"]), None)
    if still is None:
        raise ToolError(f"La fenêtre « {window['titre']} » n'existe plus.")
    text, shots = _observe(cid, still, capture=False)
    return _ok(_truncate(f"Fait : {p['preview'].split(chr(10))[0]}.\n\nAprès l'action :\n{text}"))


def _act(p, cid):
    if p["action"] == "restaurer":
        return _restore(p, cid)
    state = (conversations.get(cid) or {}).get("screen_state")
    window = _target_window(state, _screen_allow(cid))  # de nouveau : du temps a passé pendant ta décision
    hwnd = window["id"]
    # La fenêtre et ce qui est ouvert par-dessus (menu, boîte de dialogue) : la cible peut être dans l'un ou l'autre
    owners = {hwnd, *(pop["id"] for pop in _popups(window))}
    if screen.foreground() not in owners:  # remettre la fenêtre devant refermerait le menu ouvert
        screen.bring_to_front(hwnd)
    for point in [pt for pt in (p.get("point"), p.get("to")) if pt]:
        if screen.owner_at(*point) not in owners:
            raise ToolError("Une autre fenêtre recouvre la cible : je n'ai rien fait. Observe à nouveau.")
    action = p["action"]
    if action in ("cliquer", "double_clic", "clic_droit"):
        screen.click(*p["point"], button="droit" if action == "clic_droit" else "gauche", double=action == "double_clic")
    elif action == "taper":
        if p.get("point"):
            screen.click(*p["point"])
            time.sleep(0.15)
        if screen.focused_is_password():
            raise ToolError("Le curseur est dans un champ mot de passe : c'est à l'utilisateur de le remplir.")
        if p["remplacer"]:
            # Tout sélectionner, pour que le texte tapé remplace l'ancien : Ctrl+A n'existe pas partout (anciennes
            # zones de texte WinForms), Ctrl+Fin puis Ctrl+Maj+Origine marche sur une ou plusieurs lignes
            screen.press("ctrl+fin")
            screen.press("ctrl+maj+debut")
        screen.type_text(p["texte"])
    elif action == "touches":
        screen.press(p["touches"])
    elif action == "defiler":
        left, top, right, bottom = window["rect"]
        x, y = p.get("point") or ((left + right) / 2, (top + bottom) / 2)
        screen.scroll(x, y, p["direction"], p["quantite"])
    elif action == "glisser":
        screen.drag(*p["point"], *p["to"])
    time.sleep(screen.SETTLE_S + p["attente"])
    done = p["preview"].split("\n")[0]
    listed = screen.windows()
    still = next((w for w in listed if w["id"] == hwnd), None)
    if still is None or still["reduite"]:
        conversations.update(cid, lambda conv: conv.pop("screen_state", None))
        return _ok(f"Fait : {done}.\nLa fenêtre « {window['titre']} » s'est fermée ou réduite.\n\n"
                   + screen.describe_windows(listed))
    text, shots = _observe(cid, still, capture=bool(state.get("capture")))
    # La même action déjà faite dans cette tâche : dit noir sur blanc (un petit modèle reclique « pour voir », puis
    # affirme n'avoir cliqué qu'une fois)
    conv = conversations.get(cid) or {}
    before = sum(1 for m in conv.get("messages", []) if m.get("name") == "agir_ecran" and m.get("task_id") == conv.get("task_id")
                 and (m.get("content") or "").startswith(f"Fait : {done}."))
    repeat = f" ({before + 1}e fois dans cette tâche)" if before else ""
    text = _truncate(f"Fait : {done}.{repeat}\n\nAprès l'action :\n{text}")
    text += "\n\nSi la demande est remplie, rends compte à l'utilisateur sans autre action."
    return _ok(text, **({"images": shots} if shots else {}))


async def run_screen_action(prepared, cid):
    try:
        return await asyncio.to_thread(_act, prepared, cid)
    except screen.ScreenError as exc:
        raise ToolError(str(exc))


def prepare_web_fetch(root, args):
    url = (args.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise ToolError("« url » doit commencer par http:// ou https://.")
    return {"url": url, "preview": f"Lire la page : {url}"}


async def run_web_fetch(prepared, cid):
    try:
        page = await web.fetch(prepared["url"])
    except web.WebError as exc:
        raise ToolError(str(exc))
    header = f"{page['title']}\n{page['url']}" if page["title"] else page["url"]
    return _ok(_truncate(f"{header}\n\n{page['text']}"))


def prepare_web_search(root, args):
    query = (args.get("query") or "").strip()
    if not query:
        raise ToolError("Recherche vide.")
    return {"query": query, "preview": f"Recherche web : {query}"}


async def run_web_search(prepared, cid):
    try:
        results = await web.search(prepared["query"])
    except web.WebError as exc:
        raise ToolError(str(exc))
    if not results:
        return _ok("Aucun résultat.")
    lines = [f"{n}. {r['title']}\n   {r['url']}\n   {r['snippet']}" for n, r in enumerate(results, 1)]
    return _ok("\n".join(lines))


# --- registre --------------------------------------------------------------------

_PATH = {"type": "string", "description": "Chemin relatif au dossier de travail."}

TOOLS = {t.name: t for t in [
    Tool(
        "list_dir", "read",
        "Liste le contenu d'un dossier (sous-dossiers suivis de /, taille des fichiers).",
        _schema({"path": {**_PATH, "default": "."}}, []),
        prepare_list_dir, run_prepared_output,
    ),
    Tool(
        "read_file", "read",
        "Lit un fichier texte avec numéros de ligne. Pour un gros fichier, lis-le par morceaux.",
        _schema({
            "path": _PATH,
            "start_line": {"type": "integer", "description": "Première ligne (à partir de 1).", "default": 1},
            "max_lines": {"type": "integer", "description": "Nombre de lignes (1000 max).", "default": 400},
        }, ["path"]),
        prepare_read_file, run_prepared_output,
    ),
    Tool(
        "find_files", "read",
        "Cherche des fichiers par motif de nom (ex. *.py, *config*, src/*.js).",
        _schema({"pattern": {"type": "string"}, "path": {**_PATH, "default": "."}}, ["pattern"]),
        prepare_find_files, run_prepared_output,
    ),
    Tool(
        "grep", "read",
        "Cherche une expression régulière dans le contenu des fichiers texte.",
        _schema({
            "pattern": {"type": "string", "description": "Expression régulière Python."},
            "path": {**_PATH, "default": "."},
            "file_pattern": {"type": "string", "description": "Filtre sur le nom de fichier, ex. *.py.", "default": "*"},
        }, ["pattern"]),
        prepare_grep, run_prepared_output,
    ),
    Tool(
        "write_file", "write",
        "Crée un fichier ou remplace entièrement son contenu. Pour une petite modification, préfère edit_file.",
        _schema({"path": _PATH, "content": {"type": "string", "description": "Contenu complet du fichier."}}, ["path", "content"]),
        prepare_write_file, run_write,
    ),
    Tool(
        "edit_file", "write",
        "Remplace un passage exact et unique d'un fichier. Lis le fichier avant pour copier le texte exact.",
        _schema({
            "path": _PATH,
            "old_string": {"type": "string", "description": "Texte exact à remplacer (doit être unique)."},
            "new_string": {"type": "string", "description": "Nouveau texte."},
            "replace_all": {"type": "boolean", "default": False,
                            "description": "Remplacer toutes les occurrences (pour un renommage)."},
        }, ["path", "old_string", "new_string"]),
        prepare_edit_file, run_write,
    ),
    Tool(
        "multi_edit", "write",
        "Plusieurs remplacements dans un même fichier, en un seul appel et un seul accord. Appliqués dans "
        f"l'ordre, chacun sur le résultat du précédent ; tout ou rien. {MAX_EDITS} au plus.",
        _schema({
            "path": _PATH,
            "edits": {
                "type": "array",
                "maxItems": MAX_EDITS,
                "items": {
                    "type": "object",
                    "properties": {
                        "old_string": {"type": "string", "description": "Texte exact à remplacer (unique, sauf replace_all)."},
                        "new_string": {"type": "string", "description": "Nouveau texte."},
                        "replace_all": {"type": "boolean", "default": False,
                                        "description": "Remplacer toutes les occurrences."},
                    },
                    "required": ["old_string", "new_string"],
                },
            },
        }, ["path", "edits"]),
        prepare_multi_edit, run_write,
    ),
    Tool(
        "run_command", "command",
        f"Exécute une commande {POWERSHELL_NAME} dans le dossier de travail (arrêtée après {COMMAND_TIMEOUT_S} s). "
        "Pour un serveur ou une commande qui ne s'arrête pas d'elle-même, mets background à true.",
        _schema({
            "command": {"type": "string"},
            "background": {"type": "boolean", "default": False,
                           "description": "Lance en arrière-plan et rend la main tout de suite (serveur, watcher, "
                                          "long build). Renvoie un id pour command_output et stop_command."},
            "keep": {"type": "boolean", "default": False,
                     "description": "Garder la commande après la fin de la tâche : seulement si l'utilisateur "
                                    "l'a demandé (ex. « laisse le serveur tourner »)."},
        }, ["command"]),
        prepare_run_command, run_command,
    ),
    Tool(
        "command_output", "state",
        "Lit la sortie apparue depuis la dernière lecture d'une commande en arrière-plan, et son état. "
        "wait_for attend qu'un texte apparaisse (ex. « Serveur prêt »), timeout secondes au plus (30 max).",
        _schema({
            "id": {"type": "string", "description": "Id de la commande (ex. bg1)."},
            "wait_for": {"type": "string", "description": "Texte à attendre dans la sortie (facultatif)."},
            "timeout": {"type": "number", "description": "Attente maximale en secondes (15 par défaut avec wait_for)."},
        }, ["id"]),
        prepare_command_output, run_command_output,
    ),
    Tool(
        "stop_command", "state",
        "Arrête une commande lancée en arrière-plan (et tous ses processus enfants).",
        _schema({"id": {"type": "string", "description": "Id de la commande (ex. bg1)."}}, ["id"]),
        prepare_stop_command, run_stop_command,
    ),
    Tool(
        "update_todo", "state",
        "Écrit ou met à jour ton plan de travail (liste d'étapes visible par l'utilisateur). "
        "Envoie la liste complète à chaque fois, avec une seule étape in_progress.",
        _schema({
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "Étape courte, à l'impératif."},
                        "status": {"type": "string", "enum": list(TODO_MARKS)},
                    },
                    "required": ["content", "status"],
                },
            },
        }, ["items"]),
        prepare_update_todo, run_update_todo,
    ),
    Tool(
        "load_skill", "read",
        "Charge la méthode d'une compétence (voir la liste des compétences) : à appeler avant de répondre à une "
        "demande qui y correspond. Avec le paramètre file, un de ses fichiers d'appoint.",
        _schema({
            "name": {"type": "string", "description": "Nom de la compétence."},
            "file": {"type": "string", "description": "Fichier d'appoint de la compétence (facultatif)."},
        }, ["name"]),
        prepare_load_skill, run_prepared_output,
    ),
    Tool(
        "lire_fenetre", "screen",
        "Écran du PC, en texte : sans argument, la liste des fenêtres ouvertes ; avec une fenêtre (id, morceau du "
        "titre ou programme), ses éléments numérotés (boutons, champs, cases, menus) et ses textes affichés. "
        "Rapide et sans image : commence par lui.",
        _schema({"fenetre": {"type": "string", "description": "Id, morceau du titre ou programme (facultatif)."}}, []),
        prepare_screen, run_read_window,
    ),
    Tool(
        "voir_ecran", "screen",
        "Capture d'une fenêtre (et de ses menus ou boîtes de dialogue ouverts), avec ses éléments encadrés et "
        "numérotés comme dans lire_fenetre, ou de tous les écrans sans argument. Avec « zone » : recapture une partie "
        "de la dernière capture en pleine résolution, agrandie, pour lire un petit texte. Coûte environ 1 000 tokens : "
        "seulement quand le texte ne suffit pas.",
        _schema({"fenetre": {"type": "string", "description": "Id, morceau du titre ou programme (facultatif)."},
                 "zone": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4,
                          "description": "[x0, y0, x1, y1] sur la dernière capture : la partie à agrandir."}}, []),
        prepare_screen, run_see_screen,
    ),
    Tool(
        "agir_ecran", "screen_action",
        "Agit dans la dernière fenêtre observée (lire_fenetre ou voir_ecran), avec l'accord de l'utilisateur à chaque "
        "fois : cliquer, double_clic ou clic_droit sur un élément (son numéro) ou un point (x, y de la dernière "
        "capture) ; taper un texte (dans un élément : il clique d'abord dedans, ce qui défait une sélection ; "
        "remplacer=true pour remplacer tout son contenu ; ou là où est le curseur) ; touches (« ctrl+s », « entree », "
        "« alt+tab ») ; defiler (haut, bas, gauche, droite) ; glisser d'un élément ou point vers un autre ; "
        "restaurer une fenêtre réduite (« fenetre » : son nom, sans observation préalable). Les menus et boîtes de "
        "dialogue ouverts par la fenêtre font partie de l'observation : leurs éléments sont numérotés en tête. "
        "Renvoie ensuite la fenêtre telle qu'elle est devenue. Une action à la fois.",
        _schema({
            "action": {"type": "string", "enum": list(SCREEN_ACTIONS)},
            "element": {"type": "integer", "description": "Numéro de l'élément visé (dernière observation)."},
            "x": {"type": "number", "description": "Abscisse sur la dernière capture, si aucun élément ne convient."},
            "y": {"type": "number", "description": "Ordonnée sur la dernière capture."},
            "texte": {"type": "string", "description": "Texte à taper (action taper)."},
            "remplacer": {"type": "boolean", "description": "Taper : remplace tout le contenu du champ au lieu d'ajouter."},
            "touches": {"type": "string", "description": "Touches à presser ensemble (action touches)."},
            "direction": {"type": "string", "enum": list(SCROLL_DIRECTIONS), "description": "Sens du défilement."},
            "quantite": {"type": "integer", "description": "Crans de molette (défaut 3)."},
            "vers_element": {"type": "integer", "description": "Élément où lâcher (action glisser)."},
            "vers_x": {"type": "number"}, "vers_y": {"type": "number"},
            "attente": {"type": "number", "description": "Secondes à attendre avant d'observer le résultat (10 au plus)."},
            "fenetre": {"type": "string", "description": "Fenêtre à rouvrir (action restaurer) : id, titre ou programme."},
        }, ["action"]),
        prepare_screen_action, run_screen_action, bind_screen_action,
    ),
    Tool(
        "web_search", "web",
        "Recherche sur le web (via le SearXNG de l'utilisateur). Renvoie titres, liens et extraits.",
        _schema({"query": {"type": "string"}}, ["query"]),
        prepare_web_search, run_web_search,
    ),
    Tool(
        "web_fetch", "web",
        "Lit le texte d'une page web publique (http/https).",
        _schema({"url": {"type": "string"}}, ["url"]),
        prepare_web_fetch, run_web_fetch,
    ),
]}

SCHEMAS = [
    {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
    for t in TOOLS.values()
]
