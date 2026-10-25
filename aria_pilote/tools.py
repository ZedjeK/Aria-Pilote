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
from dataclasses import dataclass
from typing import Awaitable, Callable

from . import background, backups, checks, conversations, jobs, skills, web, workspace

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
    kind: str  # read | state | write | command | web
    description: str
    parameters: dict
    prepare: Callable[[str, dict], dict]
    run: Callable[[dict, str], Awaitable[dict]]


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
    conversations.update(cid, lambda c: c.setdefault("agent", {}).update(todo=items))
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
