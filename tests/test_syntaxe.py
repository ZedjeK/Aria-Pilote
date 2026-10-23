"""Vérification automatique de la syntaxe après chaque écriture (jamais d'exécution)."""

import asyncio
import shutil
import time
import unittest

from base import new_conversation, workspace

from aria_pilote import checks, config, tools

HAS_POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")

CASES = [
    ("a.py", "def f(x):\n    return x * 2\n", "def f(x:\n    return x\n", "Python", 1),
    ("a.json", '{"port": 8443}', '{\n  // port\n  "port": 8443\n}', "JSON", 2),
    ("a.toml", "port = 8443\n", "port = \n", "TOML", None),
    ("a.xml", "<a><b/></a>", "<a><b></a>", "XML", 1),
    ("a.yaml", "services:\n  web:\n    image: nginx\n", "services:\n  web:\n   - image: nginx\n    ports: [80\n", "YAML", None),
]


class SyntaxCheck(unittest.TestCase):
    def setUp(self):
        self.root = workspace()
        self.cid = new_conversation(self.root)

    def write(self, name, content):
        tool = tools.TOOLS["write_file"]
        return asyncio.run(tool.run(tool.prepare(str(self.root), {"path": name, "content": content}), self.cid))

    def check_pair(self, name, good, bad, label, line):
        result = self.write(name, good)
        self.assertIs(result.get("syntax"), True)
        self.assertIn("✓ Syntaxe vérifiée", result["output"])
        result = self.write(name, bad)
        self.assertIs(result.get("syntax"), False)
        self.assertEqual(result["status"], "ok")  # le fichier est écrit quand même
        self.assertIn(f"{label} invalide", result["output"])
        if line:
            self.assertIn(f"ligne {line}", result["output"])
        self.assertEqual((self.root / name).read_text(encoding="utf-8"), bad)

    def test_formats(self):
        for case in CASES:
            with self.subTest(format=case[3]):
                self.check_pair(*case)

    def test_ligne_fautive_montree(self):
        output = self.write("e.json", '{\n  "port": 8443, // le port\n}')["output"]
        self.assertIn("ligne 2, colonne 17", output)
        self.assertIn('2 |   "port": 8443, // le port', output)

    @unittest.skipUnless(HAS_POWERSHELL, "PowerShell absent")
    def test_powershell(self):
        self.check_pair("a.ps1", "function Test { Write-Output 'ok' }\n", "function Test {\n  Write-Output 'ok'\n",
                        "PowerShell", None)
        start = time.time()
        checks.check(self.root / "a.ps1")
        self.assertLess(time.time() - start, 2)

    def test_python_jamais_execute(self):
        trap = self.root / "piege.txt"
        self.write("b.py", f"open(r'{trap}', 'w').write('exécuté')\n")
        self.assertFalse(trap.exists())

    @unittest.skipUnless(HAS_POWERSHELL, "PowerShell absent")
    def test_powershell_jamais_execute(self):
        trap = self.root / "piege.txt"
        self.write("b.ps1", f"Set-Content -Path '{trap}' -Value 'exécuté'\n")
        self.assertFalse(trap.exists())

    def test_extension_inconnue_gros_fichier_option_desactivee(self):
        self.assertNotIn("syntax", self.write("notes.md", "# titre\n"))
        self.assertNotIn("syntax", self.write("gros.json", "[" + ",".join(["1"] * 1_200_000) + "]"))  # 2,4 Mo
        config.update({"auto_check": False})
        try:
            self.assertNotIn("syntax", self.write("c.json", "{cassé"))
        finally:
            config.update({"auto_check": True})


if __name__ == "__main__":
    unittest.main()
