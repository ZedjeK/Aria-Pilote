"""Exécution d'un appel d'outil, demande d'accord comprise : partagée par l'agent et les sous-agents."""

import asyncio
import json
import uuid

from . import chat, conversations, hooks, mcp_client, permissions, tools

# Types d'outils soumis à accord, et le drapeau qui les autorise pour toute la conversation
# (None : jamais d'autorisation globale)
APPROVAL_FLAGS = {"write": "auto_write", "web": "auto_web", "command": None, "screen_action": None, "mcp": None}

# Accords en attente : approval_id -> (future, conversation_id, type d'outil, dossier de travail, commande)
_approvals = {}


class ApprovalError(Exception):
    pass


def resolve_approval(approval_id, decision, rule=None):
    if decision not in ("allow", "deny", "allow_session", "allow_rule", "allow_task"):
        raise ApprovalError("Décision inconnue.")
    pending = _approvals.get(approval_id)
    if pending is None:
        raise ApprovalError("Cette demande n'est plus en attente.")
    future, cid, kind, root, command = pending
    if decision == "allow_rule":
        # « Toujours autoriser dans ce projet » : la règle doit couvrir la commande en attente
        if kind != "command":
            raise ApprovalError("Les règles ne concernent que les commandes.")
        try:
            permissions.add(root, rule or command, command)
        except permissions.RuleError as exc:
            raise ApprovalError(str(exc))
    if decision == "allow_task":
        # « Autoriser les actions ordinaires pour cette tâche » : à l'écran seulement, et la tâche suivante redemande
        if kind != "screen_action":
            raise ApprovalError("Seules les actions à l'écran s'autorisent pour toute une tâche.")
        conversations.update(cid, lambda c: c.__setitem__("screen_auto_task", c.get("task_id")))
    if decision == "allow_session":
        flag = APPROVAL_FLAGS[kind]
        if flag is None:
            raise ApprovalError("Cette action se valide une à une.")
        conversations.update(cid, lambda c: c.setdefault("agent", {}).update({flag: True}))
    if not future.done():
        future.set_result("deny" if decision == "deny" else "allow")


def parse_args(call):
    """Arguments d'un appel d'outil, ou (args bruts, résultat d'erreur) si le JSON est invalide."""
    raw = call["function"]["arguments"] or "{}"
    try:
        args = json.loads(raw)
        if not isinstance(args, dict):
            raise ValueError("objet attendu")
        return args, None
    except ValueError as exc:
        return {"_brut": raw}, {"status": "error", "output": f"Arguments JSON invalides : {exc}"}


async def run_tool(cid, root, name, args, call_id, out, allowed=None, parent_id=None):
    """Prépare, fait valider si besoin, puis exécute un outil.

    Émet l'événement « approval » éventuel ; le résultat ({status, output, …}) est placé dans out["result"].
    `allowed` limite les outils utilisables (sous-agents) ; `parent_id` rattache la demande d'accord
    à la carte d'un sous-agent dans l'interface.
    """
    if allowed is not None and name not in allowed:
        tool = None
    else:
        # Un outil MCP n'existe que s'il a été donné à la tâche (liste explicite des outils permis)
        tool = tools.TOOLS.get(name) or (mcp_client.registry.tool(name) if allowed is not None else None)
    if tool is None:
        names = ", ".join(allowed if allowed is not None else tools.TOOLS)
        out["result"] = {"status": "error", "output": f"Outil indisponible : {name}. Outils disponibles : {names}."}
        return
    try:
        prepared = await asyncio.to_thread(tool.prepare, root, args)
        if tool.bind:
            prepared = await asyncio.to_thread(tool.bind, prepared, cid)
    except tools.ToolError as exc:
        out["result"] = {"status": "error", "output": str(exc)}
        return
    except Exception as exc:  # arguments farfelus du modèle : on lui renvoie l'erreur
        out["result"] = {"status": "error", "output": f"Erreur : {exc}"}
        return

    # Hooks « avant_outil » : ils passent avant ta validation et peuvent bloquer l'action (code 2)
    before = await hooks.run("avant_outil", root, name, args) if root else []
    if before:
        yield hook_event("avant_outil", before, call_id, parent_id)
    blocker = hooks.blocking(before)
    if blocker:
        out["result"] = {"status": "denied",
                         "output": f"Action bloquée par un hook ({blocker['source']}) : {blocker['sortie'] or 'sans explication'}"}
        return

    settings = conversations.get(cid).get("agent") or {}
    decision = "allow"
    command = prepared.get("command") if tool.kind == "command" else None
    rule = permissions.find(root, command) if command and root else None  # « toujours autoriser » dans ce projet
    auto = prepared.get("auto_allowed")  # outil MCP réglé sur « Toujours autoriser »
    conv_now = conversations.get(cid) or {}
    # Action ordinaire à l'écran : d'office en mode PC entier, ou si tu l'as permis pour cette tâche
    pc_mode = conv_now.get("screen_mode") == "pc"
    screen_auto = (tool.kind == "screen_action" and prepared.get("ordinary")
                   and (pc_mode or (conv_now.get("task_id") and conv_now.get("screen_auto_task") == conv_now.get("task_id"))))
    auto = auto or screen_auto
    if tool.kind in APPROVAL_FLAGS and rule is None and not auto and not settings.get(APPROVAL_FLAGS[tool.kind] or "", False):
        approval_id = uuid.uuid4().hex[:12]
        future = asyncio.get_running_loop().create_future()
        _approvals[approval_id] = (future, cid, tool.kind, root, command)
        yield chat.event({
            "type": "approval",
            "approval_id": approval_id,
            "call_id": call_id,
            "parent_id": parent_id,
            "kind": tool.kind,
            "path": prepared.get("rel"),
            "preview": prepared["preview"],
            "command": command,
            "rule_prefix": permissions.suggest_prefix(command) if command else None,
            "ordinary": bool(prepared.get("ordinary")) if tool.kind == "screen_action" else None,
        })
        try:
            decision = await future
        finally:
            _approvals.pop(approval_id, None)
    if decision == "deny":
        out["result"] = {"status": "denied", "output": "L'utilisateur a refusé cette action."}
        return
    try:
        out["result"] = await tool.run(prepared, cid)
    except tools.ToolError as exc:
        out["result"] = {"status": "error", "output": str(exc)}
    except OSError as exc:
        out["result"] = {"status": "error", "output": f"Erreur système : {exc}"}
    if rule:
        out["result"]["rule"] = rule  # affiché sur la carte : autorisée sans te demander, par cette règle
    elif screen_auto:
        out["result"]["rule"] = ("action ordinaire, autorisée d'office en mode PC entier" if pc_mode
                                 else "action ordinaire autorisée pour cette tâche")
    elif auto:
        out["result"]["rule"] = "outil MCP autorisé d'office (Réglages)"

    # Hooks « apres_outil » : leur sortie complète le résultat que voit le modèle (linter, tests…)
    after = await hooks.run("apres_outil", root, name, args, out["result"]) if root else []
    if after:
        yield hook_event("apres_outil", after, call_id, parent_id)
        notes = "\n".join(f"[hook {o['source']}, code {o['code']}] {o['sortie']}" for o in after if o["sortie"])
        if notes:
            out["result"]["output"] += f"\n\n{notes}"


def hook_event(event, outcomes, call_id=None, parent_id=None):
    return chat.event({"type": "hook", "evenement": event, "call_id": call_id, "parent_id": parent_id,
                       "resultats": outcomes, "bloque": any(o["code"] == hooks.BLOCK_CODE for o in outcomes)
                       and event == "avant_outil"})
