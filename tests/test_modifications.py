"""Onglet Modifications du panneau latéral : reconstitution des diffs à partir des sauvegardes."""

import asyncio
import shutil
import unittest

from base import client, new_conversation, workspace

from aria_pilote import agent, backups, conversations, tools


class Changes(unittest.TestCase):
    def setUp(self):
        self.root = workspace()
        (self.root / "app.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
        self.cid = new_conversation(self.root)
        self.task = backups.new_task_id()
        backups.set_running(self.cid, self.task)
        self.addCleanup(backups.set_running, self.cid, None)

    def run_tool(self, name, args):
        """Comme l'agent : l'outil, puis le message de résultat dans la conversation."""
        tool = tools.TOOLS[name]
        result = asyncio.run(tool.run(tool.prepare(str(self.root), args), self.cid))
        conversations.append_message(self.cid, {"role": "tool", "name": name, "args": args, "status": "ok",
                                                "content": result["output"], "tool_call_id": name,
                                                "task_id": self.task, "backup_id": result["backup_id"],
                                                "syntax": result.get("syntax")})
        return result

    def changes(self):
        return agent.changes(conversations.get(self.cid))

    def test_diff_de_chaque_modification_dans_l_ordre(self):
        self.run_tool("edit_file", {"path": "app.py", "old_string": "x = 1", "new_string": "x = 10"})
        self.run_tool("multi_edit", {"path": "app.py", "edits": [{"old_string": "y = 2", "new_string": "y = 20"}]})
        self.run_tool("write_file", {"path": "nouveau.txt", "content": "bonjour\n"})
        result = self.changes()
        items = result["tasks"][0]["changes"]
        self.assertEqual([(c["tool"], c["path"]) for c in items],
                         [("edit_file", "app.py"), ("multi_edit", "app.py"), ("write_file", "nouveau.txt")])
        # Chaque diff ne montre que SA modification, même pour un fichier modifié deux fois
        self.assertIn("-x = 1", items[0]["diff"])
        self.assertIn("+x = 10", items[0]["diff"])
        self.assertNotIn("y = 20", items[0]["diff"])
        self.assertIn("-y = 2", items[1]["diff"])
        self.assertNotIn("-x = 1", items[1]["diff"])
        self.assertTrue(items[2]["created"])
        self.assertIn("+bonjour", items[2]["diff"])
        self.assertIs(items[0]["syntax"], True)
        self.assertEqual(result["active"], 3)

    def test_modification_annulee_et_tache_annulee(self):
        first = self.run_tool("edit_file", {"path": "app.py", "old_string": "x = 1", "new_string": "x = 10"})
        self.run_tool("write_file", {"path": "b.txt", "content": "b\n"})
        agent.undo(self.cid, first["backup_id"])
        items = self.changes()["tasks"][0]["changes"]
        self.assertTrue(items[0]["undone"])
        self.assertFalse(items[1]["undone"])
        self.assertEqual(self.changes()["active"], 1)
        agent.undo_task(self.cid, self.task)
        result = self.changes()
        self.assertIsNotNone(result["tasks"][0]["undone"])
        self.assertEqual(result["active"], 0)
        # Le diff garde ce qu'a fait l'agent, même une fois tout annulé
        self.assertIn("+x = 10", result["tasks"][0]["changes"][0]["diff"])
        self.assertIn("+b", result["tasks"][0]["changes"][1]["diff"])

    def test_fichier_retouche_depuis(self):
        self.run_tool("edit_file", {"path": "app.py", "old_string": "x = 1", "new_string": "x = 10"})
        (self.root / "app.py").write_text("x = 10\ny = 2\n# retouché à la main\n", encoding="utf-8")
        item = self.changes()["tasks"][0]["changes"][0]
        self.assertIn("retouché depuis", item["note"])
        self.assertNotIn("retouché à la main", item["diff"])  # le diff montre ce qu'a écrit l'agent, pas ta retouche

    def test_sauvegarde_expiree(self):
        result = self.run_tool("edit_file", {"path": "app.py", "old_string": "x = 1", "new_string": "x = 10"})
        shutil.rmtree(backups.BACKUP_DIR / self.cid / result["backup_id"])
        item = self.changes()["tasks"][0]["changes"][0]  # toujours listée, d'après la conversation
        self.assertEqual(item["path"], "app.py")
        self.assertIn("diff indisponible", item["diff"])

    def test_ecriture_d_un_sous_agent(self):
        tool = tools.TOOLS["write_file"]
        asyncio.run(tool.run(tool.prepare(str(self.root), {"path": "sous.txt", "content": "s\n"}), self.cid))
        item = self.changes()["tasks"][0]["changes"][0]
        self.assertEqual(item["tool"], "sous-agent")  # pas de message de l'agent principal pour elle

    def test_api(self):
        self.run_tool("edit_file", {"path": "app.py", "old_string": "x = 1", "new_string": "x = 10"})
        api = client()
        data = api.get(f"/api/agent/changes?conversation_id={self.cid}").json()
        self.assertEqual(data["active"], 1)
        self.assertEqual(api.get("/api/agent/changes?conversation_id=000000000000").status_code, 404)

    def test_conversation_sans_modification(self):
        self.assertEqual(agent.changes(conversations.get(new_conversation(self.root))), {"tasks": [], "active": 0})


if __name__ == "__main__":
    unittest.main()
