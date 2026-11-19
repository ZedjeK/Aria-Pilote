"""Reprendre d'ici : une conversation repart d'un ancien message à toi, dont le texte revient dans la saisie.

Ce qui suit ce message est mis de côté (copie gardée, 5 reprises au plus par conversation) et peut être rétabli tant que
la conversation n'a pas continué et qu'aucun fichier n'a été touché. En option, les fichiers que l'agent a modifiés dans
la partie retirée sont remis dans leur état d'avant par l'annulation par tâche (agent.undo_task) : une tâche qui a
commencé avant le message n'est jamais annulée à moitié, la case est alors refusée.
"""

import json
import uuid
from datetime import datetime

from . import agent, backups, conversations, mentions

MAX_KEPT = 5


class RewindError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def _kept_path(cid):
    return conversations.DATA_DIR / cid / "rewinds.json"  # dans le dossier de la conversation : il part avec elle


def _load(cid):
    try:
        return json.loads(_kept_path(cid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _store(cid, records):
    path = _kept_path(cid)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(records[-MAX_KEPT:], ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _chosen(conv, index):
    messages = conv["messages"]
    if not isinstance(index, int) or not 0 <= index < len(messages) or messages[index]["role"] != "user":
        raise RewindError("On ne peut reprendre que depuis un de tes messages.", 400)
    return messages[index]


def text_of(message):
    """Ce qui revient dans la saisie : le message tel que tu l'avais tapé, avec sa compétence."""
    content = message.get("content") or ""
    return f"/{message['skill']} {content}".strip() if message.get("skill") else content


def preview(conv, index):
    """Ce que reprendre d'ici retirerait et ce que remettre les fichiers ferait, sans rien toucher."""
    chosen = _chosen(conv, index)
    cut = conv["messages"][index:]
    before = {m["task_id"] for m in conv["messages"][:index] if m.get("task_id")}
    undone = conv.get("undone_tasks") or {}
    order, writes = [], set()
    for m in cut:
        task_id = m.get("task_id")
        if not task_id:
            continue
        if task_id not in order:
            order.append(task_id)
        if agent.backup_ids(m):
            writes.add(task_id)
    tasks = []
    for task_id in order:
        files = agent.task_preview(conv, task_id)["files"] if task_id in writes and task_id not in undone else []
        tasks.append({"task_id": task_id, "straddles": task_id in before, "files": files,
                      "commands": agent.task_commands(conv, task_id, cut)})
    restorable = [t for t in tasks if t["files"]]
    straddling = [t["task_id"] for t in restorable if t["straddles"]]
    summary = conv.get("summary")
    tokens = set(mentions.tokens(chosen.get("content") or ""))
    lost = [f["name"] for f in chosen.get("files") or [] if f["name"] not in tokens]  # un @fichier se relit à l'envoi
    return {"index": index, "removed": len(cut), "tasks": tasks, "straddling": straddling,
            "can_restore_files": bool(restorable) and not straddling,
            "summary_dropped": bool(summary and index < summary.get("upto", 0)),
            "lost_attachments": lost + [f"image {i}" for i, _ in enumerate(chosen.get("images") or [], 1)],
            "sandbox": bool(conv.get("sandbox"))}


def apply(cid, index, restore_files=False, force=False):
    """Coupe la conversation au message `index`. Renvoie {conversation, text, restored}."""
    conv = conversations.get(cid)
    if conv is None:
        raise RewindError("Conversation introuvable.", 404)
    if backups.is_running(cid):
        raise RewindError("L'agent travaille encore dans cette conversation : attends la fin ou arrête-le.")
    info = preview(conv, index)
    if restore_files and info["straddling"]:
        raise RewindError("Une tâche a commencé avant ce message : ses fichiers ne sont pas remis d'ici (annule-la en "
                          "entier depuis le panneau Modifications).")
    if restore_files and not info["can_restore_files"]:
        raise RewindError("Aucun fichier à remettre dans la partie retirée.")
    restored = {}
    if restore_files:
        for task in info["tasks"]:
            if task["files"]:
                restored[task["task_id"]] = agent.undo_task(cid, task["task_id"], force)
        conv = conversations.get(cid)  # l'annulation marque les cartes « annulé » dans la conversation
    files_changed = any(r.get("restored") or r.get("deleted") for r in restored.values())
    messages = conv["messages"]
    chosen, cut = messages[index], messages[index:]
    cut_tasks = {m["task_id"] for m in cut if m.get("task_id")}
    summary = conv.get("summary")
    dropped = bool(summary and index < summary.get("upto", 0))
    record = {"id": uuid.uuid4().hex[:8], "created_at": datetime.now().isoformat(timespec="seconds"), "from": index,
              "messages": cut, "summary": summary if dropped else None, "todo": conv.get("todo"),
              "plan": conv.get("plan"), "task_id": conv.get("task_id"), "files_changed": files_changed,
              "undone_tasks": {t: v for t, v in (conv.get("undone_tasks") or {}).items() if t in cut_tasks}}
    _store(cid, _load(cid) + [record])  # d'abord la copie : si la suite échoue, rien n'est perdu

    def change(c):
        c["messages"] = c["messages"][:index]
        if dropped:
            c.pop("summary", None)  # il couvrait des messages qui reviennent dans le prompt tels quels
        c.pop("todo", None)
        remaining = [m["task_id"] for m in c["messages"] if m.get("task_id")]
        if remaining:
            c["task_id"] = remaining[-1]
        else:
            c.pop("task_id", None)
        if (c.get("plan") or {}).get("task_id") not in remaining:
            c.pop("plan", None)
        if c.get("undone_tasks"):
            c["undone_tasks"] = {t: v for t, v in c["undone_tasks"].items() if t not in cut_tasks}
        c["rewound"] = {"id": record["id"], "count": len(cut), "from": index, "files_changed": files_changed}

    conv = conversations.update(cid, change)
    return {"conversation": conv, "text": text_of(chosen), "restored": restored}


def restore(cid, rewind_id):
    """Rétablit ce qu'une reprise avait mis de côté : seulement si la conversation n'a pas continué depuis et si aucun
    fichier n'a été remis (on ne sait pas rejouer ces modifications)."""
    conv = conversations.get(cid)
    if conv is None:
        raise RewindError("Conversation introuvable.", 404)
    if backups.is_running(cid):
        raise RewindError("L'agent travaille encore dans cette conversation : attends la fin ou arrête-le.")
    records = _load(cid)
    record = next((r for r in records if r["id"] == rewind_id), None)
    if record is None:
        raise RewindError("Cette reprise n'existe plus.", 404)
    if record["files_changed"]:
        raise RewindError("Des fichiers ont été remis dans leur état d'avant : le rétablissement ne les rejouerait pas.")
    if len(conv["messages"]) != record["from"]:
        raise RewindError("La conversation a continué depuis : on ne peut plus rétablir ce qui a été mis de côté.")

    def change(c):
        c["messages"] = c["messages"] + record["messages"]
        for key in ("summary", "todo", "plan", "task_id"):
            if record.get(key):
                c[key] = record[key]
        if record.get("undone_tasks"):
            c.setdefault("undone_tasks", {}).update(record["undone_tasks"])
        c.pop("rewound", None)

    conv = conversations.update(cid, change)
    _store(cid, [r for r in records if r["id"] != rewind_id])
    return {"conversation": conv}
