"""multi_edit et replace_all : plusieurs remplacements, un seul accord, tout ou rien."""

import asyncio
import unittest

from base import new_conversation, workspace

from aria_pilote import agent, tools

SOURCE = "TAUX = 0.20\n\n\ndef calc_tva(ht):\n    return ht * TAUX\n\n\ndef ttc(ht):\n    return ht + calc_tva(ht)\n"


class MultiEdit(unittest.TestCase):
    def setUp(self):
        self.root = workspace()
        self.file = self.root / "calculs.py"
        self.file.write_text(SOURCE, encoding="utf-8")
        self.cid = new_conversation(self.root)

    def run_tool(self, name, args):
        tool = tools.TOOLS[name]
        return asyncio.run(tool.run(tool.prepare(str(self.root), {"path": "calculs.py", **args}), self.cid))

    def prepare(self, name, args):
        return tools.TOOLS[name].prepare(str(self.root), {"path": "calculs.py", **args})

    def text(self):
        return self.file.read_text(encoding="utf-8")

    def test_remplacements_dans_l_ordre(self):
        # Le 2e remplacement porte sur le résultat du 1er
        self.run_tool("multi_edit", {"edits": [
            {"old_string": "TAUX = 0.20", "new_string": "TAUX = 0.055"},
            {"old_string": "0.055", "new_string": "0.10"},
        ]})
        self.assertTrue(self.text().startswith("TAUX = 0.10\n"))

    def test_renommage_avec_replace_all(self):
        result = self.run_tool("multi_edit", {"edits": [
            {"old_string": "calc_tva", "new_string": "calculer_tva", "replace_all": True},
            {"old_string": "def ttc(", "new_string": "def total_ttc("},
        ]})
        self.assertNotIn("calc_tva", self.text())
        self.assertEqual(self.text().count("calculer_tva"), 2)
        self.assertIn("def total_ttc(", self.text())
        self.assertIs(result.get("syntax"), True)  # vérification de syntaxe faite une fois, à la fin

    def test_tout_ou_rien(self):
        with self.assertRaises(tools.ToolError) as ctx:
            self.prepare("multi_edit", {"edits": [
                {"old_string": "TAUX = 0.20", "new_string": "TAUX = 0.10"},
                {"old_string": "introuvable", "new_string": "x"},
            ]})
        self.assertIn("Remplacement n° 2", str(ctx.exception))
        self.assertIn("Aucun remplacement n'a été appliqué", str(ctx.exception))
        self.assertEqual(self.text(), SOURCE)

    def test_passage_ambigu_refuse_sans_replace_all(self):
        with self.assertRaisesRegex(tools.ToolError, "apparaît 2 fois.*replace_all"):
            self.prepare("multi_edit", {"edits": [{"old_string": "calc_tva", "new_string": "x"}]})

    def test_un_seul_apercu_une_seule_sauvegarde_une_seule_annulation(self):
        prepared = self.prepare("multi_edit", {"edits": [
            {"old_string": "TAUX = 0.20", "new_string": "TAUX = 0.10"},
            {"old_string": "def ttc(", "new_string": "def total_ttc("},
        ]})
        self.assertIn("-TAUX = 0.20", prepared["preview"])
        self.assertIn("+def total_ttc(ht):", prepared["preview"])
        result = asyncio.run(tools.TOOLS["multi_edit"].run(prepared, self.cid))
        agent.undo(self.cid, result["backup_id"])
        self.assertEqual(self.text(), SOURCE)  # une seule annulation défait tout

    def test_fichier_en_crlf(self):
        self.file.write_bytes(SOURCE.replace("\n", "\r\n").encode("utf-8"))
        self.run_tool("multi_edit", {"edits": [{"old_string": "def ttc(ht):\n    return", "new_string": "def ttc(ht):\n    # TTC\n    return"}]})
        self.assertIn(b"def ttc(ht):\r\n    # TTC\r\n    return", self.file.read_bytes())

    def test_fichier_change_depuis_l_apercu(self):
        prepared = self.prepare("multi_edit", {"edits": [{"old_string": "TAUX = 0.20", "new_string": "TAUX = 0.10"}]})
        self.file.write_text(SOURCE + "# retouché\n", encoding="utf-8")
        with self.assertRaisesRegex(tools.ToolError, "a changé depuis l'aperçu"):
            asyncio.run(tools.TOOLS["multi_edit"].run(prepared, self.cid))

    def test_limites(self):
        for edits in ([], "pas une liste", [{"old_string": "TAUX", "new_string": "T"}] * (tools.MAX_EDITS + 1)):
            with self.subTest(edits=str(edits)[:30]), self.assertRaises(tools.ToolError):
                self.prepare("multi_edit", {"edits": edits})

    def test_edit_file_accepte_aussi_replace_all(self):
        self.run_tool("edit_file", {"old_string": "calc_tva", "new_string": "calculer_tva", "replace_all": True})
        self.assertNotIn("calc_tva", self.text())

    def test_multi_edit_bloque_par_le_verrou_du_plan(self):
        self.assertIn(tools.TOOLS["multi_edit"].kind, agent.LOCKED_KINDS)


if __name__ == "__main__":
    unittest.main()
