"""Exe portable : le vrai lanceur compilé, avec une petite archive embarquée, installe et met à jour un dossier de test
sans jamais toucher aux affaires de l'utilisateur ni aux moteurs déjà installés."""

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from base import SRC

sys.path.insert(0, str(SRC / "tools"))
import build_portable  # noqa: E402

# Un port où rien ne tourne : le lanceur ne doit arrêter aucune Aria ouverte à côté
CONFIG = json.dumps({"app_port": 8797})


@unittest.skipUnless(sys.platform == "win32" and build_portable.CSC.exists(), "lanceur Windows (csc.exe)")
class ExePortable(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="aria-exe-"))
        cls.launcher = cls.tmp / "lanceur.exe"
        build_portable.compile_launcher(cls.launcher)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(dir=self.tmp))
        self.home = self.dir / ".Aria-Pilote"

    def exe(self, version, files=None):
        """Un exe portable dont l'archive contient une fausse appli (code, SearXNG, moteur)."""
        staging = self.dir / f"staging-{version}"
        files = files or {"python/pythonw.exe": "faux python", "aria_pilote/__init__.py": f'VERSION = "{version}"',
                          "tools/outil.py": "", "requirements.txt": "", "services/searxng/settings.yml": "port: 8888",
                          "engines/llama.cpp/llama-server.exe": "nouveau moteur"}
        for name, text in files.items():
            (staging / name).parent.mkdir(parents=True, exist_ok=True)
            (staging / name).write_text(text, encoding="utf-8")
        payload = build_portable.make_payload(staging, self.dir / f"payload-{version}.zip", version)
        return build_portable.write_exe(self.launcher, payload, self.dir / f"Aria-Pilote-{version}.exe")

    def run_exe(self, exe):
        return subprocess.run([str(exe), "--home", str(self.home), "--install-only"],
                              capture_output=True, text=True, encoding="utf-8", timeout=60)

    def write(self, name, text):
        path = self.home / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def read(self, name):
        return (self.home / name).read_text(encoding="utf-8")

    def installed_0_23(self):
        """Une version 0.23.1 installée, avec les affaires de l'utilisateur et ses moteurs."""
        for name, text in {"config.json": CONFIG, "data/conversations/a.json": "{}", "models/m.gguf": "GGUF",
                           "skills/perso/SKILL.md": "ma compétence", "mcp.json": "{}",
                           "aria_pilote/__init__.py": 'VERSION = "0.23.1"', "aria_pilote/ancien.py": "",
                           "services/searxng/settings.yml": "secret_key: \"le-mien\"",
                           "engines/llama.cpp/llama-server.exe": "mon moteur", ".version": "0.23.1"}.items():
            self.write(name, text)

    def test_premiere_installation(self):
        r = self.run_exe(self.exe("0.24.0"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.read(".version"), "0.24.0")
        self.assertIn("0.24.0", self.read("aria_pilote/__init__.py"))
        self.assertEqual(self.read("engines/llama.cpp/llama-server.exe"), "nouveau moteur")
        self.assertTrue((self.home / "data").is_dir() and (self.home / "models").is_dir())
        # Clé secrète de SearXNG propre à ce PC, tirée au lancement
        settings = self.read("services/searxng/settings.yml")
        self.assertRegex(settings, r'secret_key: "[0-9a-f]{64}"')
        self.assertNotIn("{secret}", settings)
        self.assertFalse((self.home / ".maj").exists() or (self.home / ".ancien").exists())

    def test_mise_a_jour_garde_tes_affaires(self):
        self.installed_0_23()
        r = self.run_exe(self.exe("0.24.0"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.read(".version"), "0.24.0")
        self.assertIn("0.24.0", self.read("aria_pilote/__init__.py"))
        self.assertFalse((self.home / "aria_pilote/ancien.py").exists())  # le code est remplacé en entier
        for name, text in {"config.json": CONFIG, "data/conversations/a.json": "{}", "models/m.gguf": "GGUF",
                           "skills/perso/SKILL.md": "ma compétence", "mcp.json": "{}",
                           "services/searxng/settings.yml": "secret_key: \"le-mien\"",
                           "engines/llama.cpp/llama-server.exe": "mon moteur"}.items():
            self.assertEqual(self.read(name), text, name)

    def test_meme_version_ou_plus_recente_non_touchee(self):
        self.installed_0_23()
        self.write(".version", "0.25.0")
        r = self.run_exe(self.exe("0.24.0"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.read(".version"), "0.25.0")
        self.assertTrue((self.home / "aria_pilote/ancien.py").exists())

    def test_fichier_utilise_retour_arriere(self):
        self.installed_0_23()
        with open(self.home / "aria_pilote" / "ancien.py", "rb"):  # un fichier ouvert : le dossier ne bouge pas
            r = self.run_exe(self.exe("0.24.0"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("encore utilisé", r.stderr)
        self.assertEqual(self.read(".version"), "0.23.1")
        self.assertIn("0.23.1", self.read("aria_pilote/__init__.py"))
        self.assertTrue((self.home / "aria_pilote/ancien.py").exists())
        self.assertEqual(self.read("services/searxng/settings.yml"), "secret_key: \"le-mien\"")

    def test_archive_et_pied(self):
        exe = self.exe("0.24.0")
        data = exe.read_bytes()
        self.assertEqual(data[-8:], build_portable.PAYLOAD_MAGIC)
        start = int.from_bytes(data[-16:-8], "little")
        self.assertEqual(data[:start], self.launcher.read_bytes())
        with zipfile.ZipFile(self.dir / "payload-0.24.0.zip") as zf:
            names = zf.namelist()
            self.assertEqual(zf.read("VERSION"), b"0.24.0")
            # Le modèle de configuration, jamais la configuration (ni sa clé) de la construction
            self.assertIn("services/searxng/settings.yml.modele", names)
            self.assertNotIn("services/searxng/settings.yml", names)
            self.assertIn("{secret}", zf.read("services/searxng/settings.yml.modele").decode())
        self.assertTrue(re.search(rb"PK\x05\x06", data[start:]))


if __name__ == "__main__":
    unittest.main()
