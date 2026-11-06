"""Lecture groupée : read_file lit plusieurs fichiers d'un coup (moins d'appels d'outils sur une longue tâche), et un
vieux résultat groupé se rogne fichier par fichier, comme autant de lectures séparées."""

import shutil
import unittest

from base import workspace

from aria_pilote import chat, context, tools


def read(root, **args):
    return tools.prepare_read_file(str(root), args)["output"]


class GroupedRead(unittest.TestCase):
    def setUp(self):
        self.root = workspace()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        for name in "abc":
            (self.root / f"{name}.py").write_text(f"def {name}():\n    return '{name}'\n", encoding="utf-8")

    def test_un_seul_fichier_inchange(self):
        out = read(self.root, path="a.py")
        self.assertTrue(out.startswith("a.py — lignes 1 à 2 sur 2\n"))
        self.assertFalse(out.startswith(context.FILE_SECTION))
        self.assertEqual(read(self.root, paths=["a.py"]), out)

    def test_plusieurs_fichiers(self):
        out = read(self.root, paths=["a.py", "b.py", "c.py", "a.py"])  # doublon lu une fois
        self.assertEqual(out.count(context.FILE_SECTION), 3)
        for name in "abc":
            self.assertIn(f"{context.FILE_SECTION}{name}.py — lignes 1 à 2 sur 2\n", out)
            self.assertIn(f"    2│     return '{name}'", out)

    def test_liste_glissee_dans_path(self):
        expected = read(self.root, paths=["a.py", "b.py"])
        self.assertEqual(read(self.root, path=["a.py", "b.py"]), expected)
        self.assertEqual(read(self.root, path='["a.py", "b.py"]'), expected)

    def test_un_fichier_absent_n_empeche_pas_les_autres(self):
        out = read(self.root, paths=["a.py", "absent.py", "../dehors.py"])
        self.assertIn("a.py — lignes 1 à 2", out)
        self.assertIn("absent.py — Fichier introuvable.", out)
        self.assertIn(f"{context.FILE_SECTION}../dehors.py — ", out)
        self.assertNotIn("dehors.py — lignes", out)

    def test_limites(self):
        with self.assertRaises(tools.ToolError):
            read(self.root)
        with self.assertRaises(tools.ToolError):
            read(self.root, paths=[f"f{n}.py" for n in range(tools.MAX_READ_FILES + 1)])

    def test_sortie_partagee_entre_les_fichiers(self):
        (self.root / "gros.py").write_text("".join(f"x_{n} = {n}\n" for n in range(900)), encoding="utf-8")
        out = read(self.root, paths=["gros.py", "a.py"], max_lines=1000)
        self.assertLessEqual(len(out), tools.MAX_OUTPUT_CHARS + 200)
        self.assertIn("(coupé : relis gros.py seul, avec start_line, pour la suite)", out)
        self.assertIn("    2│     return 'a'", out)  # le petit fichier n'est pas sacrifié


class GroupedShrink(unittest.TestCase):
    def test_chaque_fichier_rogne_comme_une_lecture_seule(self):
        sections = [f"{context.FILE_SECTION}f{n}.py — lignes 1 à 300 sur 300\n" + f"fin du fichier {n} ".rjust(3000, "x")
                    for n in range(3)]
        out = context.shrink_sections("\n\n".join(sections), 600)
        for n in range(3):  # en-tête et fin de chaque fichier gardés : rien n'a disparu d'un bloc
            self.assertIn(f"{context.FILE_SECTION}f{n}.py — lignes 1 à 300", out)
            self.assertIn(f"fin du fichier {n}", out)
        self.assertLess(len(out), 3 * 800)

    def test_une_lecture_seule_rognee_comme_avant(self):
        text = "a.py — lignes 1 à 300 sur 300\n" + "x" * 3000
        self.assertEqual(context.shrink_sections(text, 600), context.shrink(text, 600))

    def test_historique(self):
        grouped = "\n\n".join(f"{context.FILE_SECTION}f{n}.py — lignes 1 à 9 sur 9\n" + "y" * 2000 + f" fin {n}"
                              for n in range(3))
        messages = [{"role": "user", "content": "Corrige le projet."}]
        for n, (name, content) in enumerate([("read_file", grouped)] + [("grep", "z" * 3000)] * 4):
            messages += [{"role": "assistant", "content": "", "tool_calls": [{"id": f"c{n}", "function": {"name": name}}]},
                         {"role": "tool", "tool_call_id": f"c{n}", "name": name, "status": "ok", "content": content}]
        sent = [m["content"] for m in chat.build_messages({"messages": messages}, "") if m["role"] == "tool"]
        for n in range(3):  # la lecture groupée est ancienne (4 résultats après elle) : rognée, mais par fichier
            self.assertIn(f" fin {n}", sent[0])
        self.assertLess(len(sent[0]), len(grouped))


if __name__ == "__main__":
    unittest.main()
