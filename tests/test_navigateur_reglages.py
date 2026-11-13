"""Navigateur piloté, réglages : exceptions locales (vérifiées, jamais les ports d'Aria), routes de la carte des
Réglages, site local et vérificateurs du banc d'essai.

Les vrais lancements demandent Chromium (tools/install_navigateur.py) : sans lui, ils sont sautés, comme en CI.
"""

import asyncio
import http.server
import importlib.util
import json
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from base import ORIGIN, SRC, client, new_conversation

from aria_pilote import config, navigateur, tools

sys.path.insert(0, str(SRC / "tools"))
sys.path.insert(0, str(SRC / "bench"))
import bench  # noqa: E402

DEV_CHROMIUM = SRC / "chromium"


def set_exceptions(entries):
    config.update({"navigateur": {"locaux_autorises": entries}})


class Exceptions(unittest.TestCase):
    def setUp(self):
        self.addCleanup(set_exceptions, [])

    def test_liste_tapee_nettoyee(self):
        out = navigateur.check_settings({"locaux_autorises": "127.0.0.1:3000, 192.168.1.20:8080 ;[::1]:5000 "
                                                             "LOCALHOST:3000,127.0.0.1:3000"})
        self.assertEqual(out["locaux_autorises"], ["127.0.0.1:3000", "192.168.1.20:8080", "[::1]:5000",
                                                   "localhost:3000"])
        self.assertEqual(navigateur.check_settings({"locaux_autorises": ""})["locaux_autorises"], [])

    def test_entrees_refusees(self):
        for bad in ("127.0.0.1", "http://127.0.0.1:3000", "127.0.0.1:0", "127.0.0.1:70000", "a b:80", "*:80"):
            with self.assertRaises(ValueError, msg=bad):
                navigateur.check_settings({"locaux_autorises": [bad]})
        with self.assertRaises(ValueError):
            navigateur.check_settings({"locaux_autorises": [f"10.0.0.{i}:80" for i in range(25)]})

    def test_jamais_les_ports_d_aria(self):
        own = config.get()["app_port"]  # celui des tests (8799), en plus des ports par défaut
        for port in (8700, 8701, 8888, 8710, 8711, 8898, own):
            with self.assertRaises(ValueError, msg=port):
                navigateur.check_settings({"locaux_autorises": [f"127.0.0.1:{port}"]})
        # Même écrit à la main dans config.json : ignoré à l'usage
        set_exceptions([f"127.0.0.1:{own}", "127.0.0.1:8700"])
        self.assertFalse(navigateur.exception_locale("127.0.0.1", own))
        self.assertIsNotNone(asyncio.run(navigateur.refus_reseau(f"http://127.0.0.1:{own}/api/conversations")))

    def test_seul_le_port_autorise_passe(self):
        set_exceptions(["127.0.0.1:3000", "192.168.1.20:8080"])
        self.assertIsNone(asyncio.run(navigateur.refus_reseau("http://127.0.0.1:3000/app")))
        self.assertIsNone(asyncio.run(navigateur.refus_reseau("ws://127.0.0.1:3000/flux")))
        self.assertIsNone(asyncio.run(navigateur.refus_reseau("http://192.168.1.20:8080/")))
        for url in ("http://127.0.0.1:3001/", "http://localhost:3000/", "http://192.168.1.21:8080/",
                    "http://192.168.1.20/"):
            self.assertIsNotNone(asyncio.run(navigateur.refus_reseau(url)), url)
        # Retirer l'exception prend effet tout de suite (pas de cache)
        set_exceptions([])
        self.assertIsNotNone(asyncio.run(navigateur.refus_reseau("http://127.0.0.1:3000/app")))

    def test_naviguer_vers_une_exception(self):
        set_exceptions(["127.0.0.1:3000"])
        prepared = tools.prepare_naviguer(None, {"url": "http://127.0.0.1:3000/"})
        self.assertIn("exception locale", prepared["preview"])
        with self.assertRaises(tools.ToolError) as ctx:
            tools.prepare_naviguer(None, {"url": "http://127.0.0.1:3001/"})
        self.assertIn("exceptions du navigateur", str(ctx.exception))


class Routes(unittest.TestCase):
    def setUp(self):
        self.client = client()
        self.addCleanup(set_exceptions, [])

    def test_etat(self):
        s = self.client.get("/api/navigateur").json()
        for key in ("installe", "ouvert", "profil", "profil_existe", "chromium", "onglets"):
            self.assertIn(key, s)
        self.assertFalse(s["ouvert"])

    def test_exceptions_par_les_reglages(self):
        r = self.client.put("/api/config", json={"navigateur": {"locaux_autorises": "127.0.0.1:3000, 10.0.0.5:81"}},
                            headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["navigateur"]["locaux_autorises"], ["127.0.0.1:3000", "10.0.0.5:81"])
        r = self.client.put("/api/config", json={"navigateur": {"locaux_autorises": ["127.0.0.1:8700"]}},
                            headers=ORIGIN)
        self.assertEqual(r.status_code, 400)
        self.assertIn("Aria Pilote", r.json()["detail"])
        self.assertEqual(config.get()["navigateur"]["locaux_autorises"], ["127.0.0.1:3000", "10.0.0.5:81"])

    def test_fermer_et_vider_sans_navigateur(self):
        self.assertFalse(self.client.post("/api/navigateur/fermer", headers=ORIGIN).json()["ouvert"])
        navigateur.PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        s = self.client.post("/api/navigateur/vider-profil", headers=ORIGIN).json()
        self.assertFalse(s["profil_existe"])

    def test_une_autre_page_ne_peut_rien(self):
        r = self.client.post("/api/navigateur/vider-profil", headers={"Origin": "https://exemple.fr"})
        self.assertEqual(r.status_code, 403)


class FakeConfigClient:
    """Le client du banc d'essai, réduit à la config : il note les écritures."""

    def __init__(self, entries):
        self.entries, self.puts = list(entries), []

    def get(self, url):
        entries = self.entries
        return type("R", (), {"json": lambda self: {"navigateur": {"locaux_autorises": entries}}})()

    def put(self, url, json=None, headers=None):
        self.puts.append(json["navigateur"]["locaux_autorises"])
        return type("R", (), {"raise_for_status": lambda self: None})()


def load_check(name):
    spec = importlib.util.spec_from_file_location(f"check_{name}", SRC / "bench" / "tasks" / name / "check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BancDEssai(unittest.TestCase):
    def test_site_local_et_exception_temporaire(self):
        ws = Path(tempfile.mkdtemp())
        task = {"site": True, "dir": SRC / "bench" / "tasks" / "23-navigateur-formulaire"}
        fake = FakeConfigClient(["192.168.1.20:8080"])
        with bench.local_site(fake, task, ws) as site:
            self.assertRegex(site, r"^http://127\.0\.0\.1:\d+/$")
            with urllib.request.urlopen(site) as resp:
                self.assertIn("Réservation", resp.read().decode("utf-8"))
            with urllib.request.urlopen(site + "envoi?nom=Durand&ville=Rouen&mdp=") as resp:
                resp.read()
        address = site.removeprefix("http://").rstrip("/")
        self.assertEqual(fake.puts, [["192.168.1.20:8080", address], ["192.168.1.20:8080"]])  # remis comme avant
        log = json.loads((ws / "requetes.json").read_text(encoding="utf-8"))
        self.assertEqual(log[-1], {"chemin": "/envoi", "parametres": {"nom": ["Durand"], "ville": ["Rouen"],
                                                                      "mdp": [""]}})

    def test_verificateur_du_formulaire(self):
        check = load_check("23-navigateur-formulaire").check
        ws = Path(tempfile.mkdtemp())
        run = {"calls": [{"name": "taper"}, {"name": "choisir"}, {"name": "cliquer"}], "final": ""}

        def received(**params):
            entry = {"chemin": "/envoi", "parametres": {k: [v] for k, v in params.items()}}
            (ws / "requetes.json").write_text(json.dumps([entry]), encoding="utf-8")
            return check(ws, run)

        self.assertFalse(check(ws, run)[0])  # rien reçu
        self.assertTrue(received(nom="Durand", ville="Rouen", mdp="")[0])
        self.assertFalse(received(nom="Durand", ville="Rouen", mdp="1234")[0])
        self.assertFalse(received(nom="Durand", ville="Caen", mdp="")[0])

    def test_verificateur_de_la_lecture(self):
        check = load_check("22-navigateur-lire").check
        page = (SRC / "bench" / "tasks" / "22-navigateur-lire" / "site" / "index.html").read_text(encoding="utf-8")
        self.assertEqual(page.count("42"), 1)  # la bonne réponse n'apparaît qu'à un endroit
        run = {"calls": [{"name": "naviguer"}, {"name": "chercher_page"}], "final": "Il est dans la baie 42."}
        self.assertTrue(check(None, run)[0])
        self.assertFalse(check(None, {**run, "final": "Baie 27, je crois."})[0])
        self.assertFalse(check(None, {**run, "calls": [{"name": "naviguer"}]})[0])


class _Site(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = "<title>Mon appli</title><h1>Appli de dev</h1>".encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@unittest.skipUnless(sys.platform == "win32" and any(DEV_CHROMIUM.glob("chromium-*/chrome-win64/chrome.exe")),
                     "Chromium non installé (tools/install_navigateur.py)")
class VraieException(unittest.TestCase):
    def setUp(self):
        self.original = navigateur.BROWSERS_DIR, navigateur.manager
        navigateur.BROWSERS_DIR = DEV_CHROMIUM
        navigateur.manager = navigateur.Navigateur()
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Site)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.port = self.server.server_address[1]
        self.addCleanup(self.server.shutdown)
        self.addCleanup(set_exceptions, [])

    def tearDown(self):
        navigateur.BROWSERS_DIR, navigateur.manager = self.original

    def test_appli_locale_autorisee_lue(self):
        set_exceptions([f"127.0.0.1:{self.port}"])
        cid = new_conversation()

        async def scenario():
            try:
                url = f"http://127.0.0.1:{self.port}/"
                result = await tools.run_naviguer(tools.prepare_naviguer(None, {"url": url}), cid)
                self.assertIn("Mon appli", result["output"])
                self.assertIn("# Appli de dev", result["output"])
            finally:
                await navigateur.manager.fermer()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
