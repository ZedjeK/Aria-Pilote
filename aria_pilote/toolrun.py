"""Exécution d'un appel d'outil, demande d'accord comprise : partagée par l'agent et les sous-agents."""

import asyncio
import json
import uuid

from . import chat, conversations, tools

# Types d'outils soumis à accord, et le drapeau qui les autorise pour toute la conversation
# (None : jamais d'autorisation globale)
APPROVAL_FLAGS = {"write": "auto_write", "web": "auto_web", "command": None}

# Accords en attente : approval_id -> (future, conversation_id, type d'outil)
_approvals = {}


class ApprovalError(Exception):
    pass


def resolve_approval(approval_id, decision):
    if decision not in ("allow", "deny", "allow_session"):
        raise ApprovalError("Décision inconnue.")
    pending = _approvals.get(approval_id)
    if pending is None:
        raise ApprovalError("Cette demande n'est plus en attente.")
    future, cid, kind = pending
    if decision == "allow_session":
        flag = APPROVAL_FLAGS[kind]
        if flag is None:
            raise ApprovalError("Les commandes se valident une par une.")
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
    tool = tools.TOOLS.get(name) if allowed is None or name in allowed else None
    if tool is None:
        names = ", ".join(allowed if allowed is not None else tools.TOOLS)
        out["result"] = {"status": "error", "output": f"Outil indisponible : {name}. Outils disponibles : {names}."}
        return
    try:
        prepared = await asyncio.to_thread(tool.prepare, root, args)
    except tools.ToolError as exc:
        out["result"] = {"status": "error", "output": str(exc)}
        return
    except Exception as exc:  # arguments farfelus du modèle : on lui renvoie l'erreur
        out["result"] = {"status": "error", "output": f"Erreur : {exc}"}
        return

    settings = conversations.get(cid).get("agent") or {}
    decision = "allow"
    if tool.kind in APPROVAL_FLAGS and not settings.get(APPROVAL_FLAGS[tool.kind] or "", False):
        approval_id = uuid.uuid4().hex[:12]
        future = asyncio.get_running_loop().create_future()
        _approvals[approval_id] = (future, cid, tool.kind)
        yield chat.event({
            "type": "approval",
            "approval_id": approval_id,
            "call_id": call_id,
            "parent_id": parent_id,
            "kind": tool.kind,
            "path": prepared.get("rel"),
            "preview": prepared["preview"],
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
