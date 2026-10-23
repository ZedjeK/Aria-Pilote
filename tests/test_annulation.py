"""Annulation d'une tâche entière : retour à l'état d'avant, conflits, corbeille, cycle de vie de la tâche."""

import asyncio
import hashlib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import base
from base import ORIGIN, client, new_conversation, workspace

from aria_pilote import agent, backups, chat, conversations, tools
from aria_pilote.server import manager


def tree(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in Path(root).rglob("*") if p.is_file()}


def write(cid, root, name, args):
    tool = tools.TOOLS[name]
    return asyncio.run(tool.run(tool.prepare(str(root), args), cid))


class UndoTask(unittest.TestCase):
    def setUp(self):
        self.root = workspace()
        (self.root / "app.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
        (self.root / "notes.txt").write_text("notes\n", encoding="utf-8")
        self.before = tree(self.root)
        self.cid = new_conversation(self.root)
        self.task = backups.new_task_id()
        self.addCleanup(backups.set_running, self.cid, None)

    def run_task(self, task=None):
        """Une tâche : app.py modifié 3 fois, un fichier créé, notes.txt réécrit, une commande lancée."""
        task = task or self.task
        backups.set_running(self.cid, task)
        write(self.cid, self.root, "edit_file", {"path": "app.py", "old_string": "x = 1", "new_string": "x = 10"})
        write(self.cid, self.root, "edit_file", {"path": "app.py", "old_string": "y = 2", "new_string": "y = 20"})
        write(self.cid, self.root, "edit_file", {"path": "app.py", "old_string": "x = 10", "new_string": "x = 100"})
        write(self.cid, self.root, "write_file", {"path": "nouveau/module.py", "content": "print('créé')\n"})
        write(self.cid, self.root, "write_file", {"path": "notes.txt", "content": "notes réécrites\n"})
        backups.set_running(self.cid, None)
        conversations.update(self.cid, lambda c: c["messages"].append(
            {"role": "tool", "name": "run_command", "args": {"command": "pip install requests"}, "status": "ok",
             "task_id": task, "content": "", "tool_call_id": "c1"}))

    def test_apercu(self):
        self.run_task()
        preview = agent.task_preview(conversations.get(self.cid), self.task)
        self.assertEqual({f["path"]: f["action"] for f in preview["files"]},
                         {"app.py": "restaurer", "nouveau/module.py": "supprimer", "notes.txt": "restaurer"})
        self.assertEqual(preview["commands"], ["pip install requests"])
        self.assertFalse(any(f["conflict"] for f in preview["files"]))

    def test_dossier_remis_a_l_identique(self):
        self.run_task()
        sent = len(base.sent_to_bin)
        result = agent.undo_task(self.cid, self.task)
        self.assertEqual(tree(self.root), self.before)  # app.py, modifié 3 fois, revient à sa version d'origine
        self.assertEqual(len(result["restored"]), 2)
        self.assertEqual(result["deleted"], ["nouveau/module.py"])
        self.assertEqual(len(base.sent_to_bin) - sent, 3)  # versions actuelles à la corbeille
        self.assertEqual(agent.task_preview(conversations.get(self.cid), self.task)["files"], [])
        self.assertIn(self.task, conversations.get(self.cid)["undone_tasks"])

    def test_conflit_laisse_tel_quel_sauf_si_on_force(self):
        self.run_task()
        (self.root / "notes.txt").write_text("retouché par Jérémy\n", encoding="utf-8")
        preview = agent.task_preview(conversations.get(self.cid), self.task)
        self.assertEqual([f["path"] for f in preview["files"] if f["conflict"]], ["notes.txt"])
        result = agent.undo_task(self.cid, self.task)
        self.assertEqual(result["skipped"], ["notes.txt"])
        self.assertEqual((self.root / "notes.txt").read_text(encoding="utf-8"), "retouché par Jérémy\n")
        self.assertEqual((self.root / "app.py").read_text(encoding="utf-8"), "x = 1\ny = 2\n")
        result = agent.undo_task(self.cid, self.task, force=True)
        self.assertEqual(result["restored"], ["notes.txt"])
        self.assertEqual((self.root / "notes.txt").read_text(encoding="utf-8"), "notes\n")

    def test_deux_taches_independantes(self):
        t1, t2 = backups.new_task_id(), backups.new_task_id()
        backups.set_running(self.cid, t1)
        write(self.cid, self.root, "write_file", {"path": "a.txt", "content": "tâche 1\n"})
        backups.set_running(self.cid, t2)
        write(self.cid, self.root, "write_file", {"path": "b.txt", "content": "tâche 2\n"})
        backups.set_running(self.cid, None)
        agent.undo_task(self.cid, t1)
        self.assertFalse((self.root / "a.txt").exists())
        self.assertTrue((self.root / "b.txt").exists())


class TaskLifecycleApi(unittest.TestCase):
    def setUp(self):
        async def fake_run(conv, preset, model_name):
            yield chat.event({"type": "done", "message": None})

        for patcher in (mock.patch.object(agent, "run", fake_run), mock.patch.object(manager, "state", "ready"),
                        mock.patch.object(manager, "model", SimpleNamespace(id="t", name="Simu"))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.api = client()
        self.root = workspace()
        self.cid = new_conversation(self.root)

    def test_continuer_prolonge_la_tache_nouvelle_demande_en_ouvre_une(self):
        self.api.post("/api/chat", json={"conversation_id": self.cid, "content": "Première demande"})
        first = conversations.get(self.cid)["task_id"]
        self.api.post("/api/chat", json={"conversation_id": self.cid, "content": "Continue", "continue_task": True})
        self.assertEqual(conversations.get(self.cid)["task_id"], first)
        self.api.post("/api/chat", json={"conversation_id": self.cid, "content": "Autre demande"})
        self.assertNotEqual(conversations.get(self.cid)["task_id"], first)

    def test_api_apercu_refus_et_annulation(self):
        task = backups.new_task_id()
        backups.set_running(self.cid, task)
        write(self.cid, self.root, "write_file", {"path": "b.txt", "content": "x\n"})
        backups.set_running(self.cid, "abcdefabcdef")  # l'agent travaille encore
        query = f"/api/agent/undo-task?conversation_id={self.cid}&task_id={task}"
        self.assertEqual(self.api.post("/api/agent/undo-task", json={"conversation_id": self.cid, "task_id": task},
                                       headers=ORIGIN).status_code, 409)
        backups.set_running(self.cid, None)
        self.assertEqual(self.api.get(query).json()["files"][0]["path"], "b.txt")
        self.assertEqual(self.api.get(f"/api/agent/undo-task?conversation_id={self.cid}&task_id=../../x").status_code, 400)
        r = self.api.post("/api/agent/undo-task", json={"conversation_id": self.cid, "task_id": task}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200)
        self.assertFalse((self.root / "b.txt").exists())


if __name__ == "__main__":
    unittest.main()
