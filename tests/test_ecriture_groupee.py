"""Écriture groupée : multi_edit modifie plusieurs fichiers en un appel et un seul accord (moins d'appels d'outils sur
une longue tâche), tout ou rien, avec une sauvegarde et une annulation par fichier."""

import asyncio
import json
import shutil
import unittest

from base import new_conversation, workspace

from aria_pilote import agent, backups, conversations, hooks, tools

SOURCES = {"a.py": "A = 1\n", "b.py": "B = 2\n", "c.py": "C = 3\n"}


def edit(path, old, new):
    return {"path": path, "edits": [{"old_string": old, "new_string": new}]}


class GroupedWrite(unittest.TestCase):
    def setUp(self):
        self.root = workspace()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        for name, text in SOURCES.items():
            (self.root / name).write_text(text, encoding="utf-8")
        self.cid = new_conversation(self.root)
        self.task = backups.new_task_id()
        backups.set_running(self.cid, self.task)
        self.addCleanup(backups.set_running, self.cid, None)

    def prepare(self, args):
        return tools.TOOLS["multi_edit"].prepare(str(self.root), args)

    def run_tool(self, args):
        """Comme l'agent : l'outil, puis le message de résultat dans la conversation."""
        result = asyncio.run(tools.TOOLS["multi_edit"].run(self.prepare(args), self.cid))
        message = {"role": "tool", "name": "multi_edit", "args": args, "status": "ok", "content": result["output"],
                   "tool_call_id": "c1", "task_id": self.task}
        for key in ("backup_id", "backups", "syntax"):
            if result.get(key) is not None:
                message[key] = result[key]
        conversations.append_message(self.cid, message)
        return result

    def text(self, name):
        return (self.root / name).read_text(encoding="utf-8")

    def test_un_seul_fichier_inchange(self):
        args = edit("a.py", "A = 1", "A = 10")
        self.assertEqual(self.prepare({"files": [args]})["preview"], self.prepare(args)["preview"])
        result = self.run_tool({"files": [args]})
        self.assertIn("backup_id", result)
        self.assertNotIn("backups", result)
        self.assertTrue(result["output"].startswith("Fichier enregistré : a.py (1 lignes)."))

    def test_plusieurs_fichiers_un_seul_apercu(self):
        args = {"files": [edit("a.py", "A = 1", "A = 10"), edit("b.py", "B = 2", "B = 20")]}
        prepared = self.prepare(args)
        self.assertEqual(prepared["rel"], "2 fichiers : a.py, b.py")
        self.assertIn("+A = 10", prepared["preview"])
        self.assertIn("+B = 20", prepared["preview"])
        result = self.run_tool(args)
        self.assertEqual((self.text("a.py"), self.text("b.py"), self.text("c.py")), ("A = 10\n", "B = 20\n", "C = 3\n"))
        self.assertEqual([b["path"] for b in result["backups"]], ["a.py", "b.py"])
        self.assertIn("2 fichiers enregistrés", result["output"])
        self.assertIs(result["syntax"], True)

    def test_liste_envoyee_en_texte_json(self):
        files = [edit("a.py", "A = 1", "A = 10"), edit("b.py", "B = 2", "B = 20")]
        self.assertEqual(self.prepare({"files": json.dumps(files)})["rel"], "2 fichiers : a.py, b.py")

    def test_tout_ou_rien_sur_tous_les_fichiers(self):
        for files in ([edit("a.py", "A = 1", "A = 10"), edit("b.py", "introuvable", "x")],
                      [edit("a.py", "A = 1", "A = 10"), edit("absent.py", "x", "y")]):
            with self.subTest(fichier=files[1]["path"]), self.assertRaises(tools.ToolError) as ctx:
                self.prepare({"files": files})
            self.assertIn(f"{files[1]['path']} : ", str(ctx.exception))
            self.assertIn("Aucun fichier n'a été modifié", str(ctx.exception))
        self.assertEqual(self.text("a.py"), "A = 1\n")

    def test_un_fichier_change_depuis_l_apercu_bloque_tout(self):
        prepared = self.prepare({"files": [edit("a.py", "A = 1", "A = 10"), edit("b.py", "B = 2", "B = 20")]})
        (self.root / "b.py").write_text("B = 2\n# retouché\n", encoding="utf-8")
        with self.assertRaisesRegex(tools.ToolError, "b.py a changé depuis l'aperçu"):
            asyncio.run(tools.TOOLS["multi_edit"].run(prepared, self.cid))
        self.assertEqual(self.text("a.py"), "A = 1\n")  # rien d'écrit, pas même le premier

    def test_limites(self):
        with self.assertRaisesRegex(tools.ToolError, "apparaît deux fois"):
            self.prepare({"files": [edit("a.py", "A = 1", "A = 10"), edit("a.py", "A", "Z")]})
        many = [edit(f"f{n}.py", "x", "y") for n in range(tools.MAX_EDIT_FILES + 1)]
        with self.assertRaisesRegex(tools.ToolError, "au plus par appel"):
            self.prepare({"files": many})
        with self.assertRaises(tools.ToolError):
            self.prepare({"files": ["a.py", "b.py"]})
        with self.assertRaises(tools.ToolError):
            self.prepare({"files": [edit("a.py", "A = 1", "A = 10"), {"path": "b.py", "edits": []}]})

    def test_syntaxe_verifiee_par_fichier(self):
        result = self.run_tool({"files": [edit("a.py", "A = 1", "A = 10"), edit("b.py", "B = 2", "B = (")]})
        self.assertIs(result["syntax"], False)
        self.assertIn("- b.py (1 lignes).", result["output"])

    def test_annulation_par_fichier_et_en_bloc(self):
        result = self.run_tool({"files": [edit("a.py", "A = 1", "A = 10"), edit("b.py", "B = 2", "B = 20")]})
        first, second = [b["backup_id"] for b in result["backups"]]
        agent.undo(self.cid, first)
        message = conversations.get(self.cid)["messages"][-1]
        self.assertFalse(message.get("undone"))  # la carte reste active : b.py est encore modifié
        self.assertEqual((self.text("a.py"), self.text("b.py")), ("A = 1\n", "B = 20\n"))
        changes = agent.changes(conversations.get(self.cid))
        self.assertEqual([(c["path"], c["tool"], c["undone"]) for c in changes["tasks"][0]["changes"]],
                         [("a.py", "multi_edit", True), ("b.py", "multi_edit", False)])
        agent.undo(self.cid, second)
        self.assertTrue(conversations.get(self.cid)["messages"][-1]["undone"])
        self.assertEqual(agent.changes(conversations.get(self.cid))["active"], 0)

    def test_annulation_de_la_tache(self):
        self.run_tool({"files": [edit("a.py", "A = 1", "A = 10"), edit("b.py", "B = 2", "B = 20")]})
        agent.undo_task(self.cid, self.task)
        self.assertEqual((self.text("a.py"), self.text("b.py")), ("A = 1\n", "B = 2\n"))
        self.assertTrue(conversations.get(self.cid)["messages"][-1]["undone"])

    def test_fichiers_des_hooks(self):
        files = [edit("a.py", "A = 1", "A = 10"), edit("b.py", "B = 2", "B = 20")]
        self.assertEqual(hooks._files({"files": files}), "a.py\nb.py")
        self.assertEqual(hooks._files({"paths": ["a.py", "c.py"]}), "a.py\nc.py")
        self.assertEqual(hooks._files({"path": "a.py"}), "a.py")


if __name__ == "__main__":
    unittest.main()
