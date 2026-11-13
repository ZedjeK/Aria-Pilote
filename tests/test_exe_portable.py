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

    def run_exe(self, exe, *extra):
        return subprocess.run([str(exe), "--home", str(self.home), "--install-only", *extra],
                              capture_output=True, text=True, encoding="utf-8", timeout=60)

    def shortcut_target(self, lnk):
        """Cible d'un raccourci, lue par le même objet COM que celui qui l'a écrit."""
        command = f"(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}').TargetPath"
        return subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True,
                              timeout=60).stdout.strip()

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
        # Le petit lanceur, sans l'archive ; pas de raccourci pour une installation ailleurs (--home)
        self.assertEqual((self.home / "Aria-Pilote.exe").read_bytes(), self.launcher.read_bytes())
        self.assertLess((self.home / "Aria-Pilote.exe").stat().st_size, 1024 ** 2)
        self.assertFalse((self.home / ".raccourcis").exists())

    def test_raccourcis_une_seule_fois(self):
        desktop, menu = self.dir / "Bureau", self.dir / "Menu"
        dirs = ("--desktop", str(desktop), "--start-menu", str(menu))
        r = self.run_exe(self.exe("0.24.0"), *dirs)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, "")
        launcher = self.home / "Aria-Pilote.exe"
        for folder in (desktop, menu):
            # resolve() : le dossier temporaire peut être donné en nom court (RUNNER~1), le raccourci en nom long
            self.assertEqual(Path(self.shortcut_target(folder / "Aria Pilote.lnk")).resolve(), launcher.resolve())
        # Raccourci du bureau supprimé par l'utilisateur : la mise à jour ne le recrée pas
        (desktop / "Aria Pilote.lnk").unlink()
        r = self.run_exe(self.exe("0.25.0"), *dirs)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.read(".version"), "0.25.0")
        self.assertFalse((desktop / "Aria Pilote.lnk").exists())
        self.assertTrue((menu / "Aria Pilote.lnk").exists())

    def test_petit_lanceur_remis_en_place(self):
        self.run_exe(self.exe("0.24.0"))
        launcher = self.home / "Aria-Pilote.exe"
        launcher.write_bytes(b"ancien lanceur")
        r = self.run_exe(self.exe("0.25.0"))  # mise à jour : remplacé
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(launcher.read_bytes(), self.launcher.read_bytes())
        launcher.unlink()
        r = self.run_exe(self.exe("0.25.0"))  # même version, rien à installer : remis quand même
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(launcher.read_bytes(), self.launcher.read_bytes())

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

    def test_mise_a_jour_remplace_chromium(self):
        # Chromium fait partie du code : il doit suivre la version de Playwright, contrairement aux moteurs
        self.installed_0_23()
        self.write("chromium/chromium-1000/chrome-win64/chrome.exe", "ancien chromium")
        files = {"python/pythonw.exe": "faux python", "aria_pilote/__init__.py": 'VERSION = "0.24.0"',
                 "tools/outil.py": "", "requirements.txt": "", "services/searxng/settings.yml": "port: 8888",
                 "chromium/chromium-2000/chrome-win64/chrome.exe": "nouveau chromium"}
        r = self.run_exe(self.exe("0.24.0", files))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.read("chromium/chromium-2000/chrome-win64/chrome.exe"), "nouveau chromium")
        self.assertFalse((self.home / "chromium/chromium-1000").exists())
        self.assertEqual(self.read("engines/llama.cpp/llama-server.exe"), "mon moteur")

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

    def test_fenetre_fermee_pendant_l_attente(self):
        """Fermer la fenêtre de démarrage ne fait que la masquer : le lanceur attend la fin (l'appli qui répond, ou le
        délai) au lieu de conclure trop tôt « ne répond pas », et une fenêtre détruite ne le fait pas planter."""
        essai = self.tmp / "essai_fenetre.exe"
        if not essai.exists():
            build_portable.compile_launcher(essai, [Path(__file__).with_name("essai_fenetre.cs")], main="EssaiFenetre")
        for scenario, hidden in (("fermee", True), ("detruite", False)):
            with self.subTest(scenario):
                r = subprocess.run([str(essai), scenario], capture_output=True, text=True, timeout=60)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(r.stdout.strip(), f"fini=True masquee={hidden} attendu=True")


if __name__ == "__main__":
    unittest.main()
