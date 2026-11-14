"""Bac à sable, interface : routes d'état, de création (en arrière-plan, avec avancement) et de suppression
(confirmation exigée), réglage « Internet public », progression du téléchargement, et cohérence de la page (section,
puce, scripts, identifiants). Faux wsl.exe ; aucune vraie distribution n'est créée ici.
"""

import http.server
import re
import shutil
import threading
import time
import unittest

from base import HOME, ORIGIN, SRC, client
from test_bac_a_sable import FakeWsl, image_tar

from aria_pilote import bac_a_sable as bas, config

FOREIGN = {"Origin": "https://exemple.fr"}
STATIC = SRC / "aria_pilote" / "static"


def wait_for(check, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("délai dépassé")


class Base(unittest.TestCase):
    def setUp(self):
        self.fake = FakeWsl()
        self.original = bas.run_wsl, bas.download, bas._set_creation
        bas.run_wsl = self.fake
        self.image = image_tar(HOME / "image-interface.tar.gz")
        self.release = threading.Event()
        self.release.set()  # le téléchargement passe tout de suite, sauf test qui le retient
        self.progress_seen = []

        def download(key, progress=None):
            if progress:
                progress(50, 100)
            self.release.wait(10)
            return self.image

        bas.download = download
        self.reset_state()
        self.client = client()
        self.addCleanup(self.restore)

    def reset_state(self):
        bas._creation.update(etat="inactif", image=None, etape=None, octets=0, total=None, message=None)

    def restore(self):
        self.release.set()
        wait_for(lambda: bas.creation_state()["etat"] != "en cours")  # aucune tâche de fond ne survit au test
        bas.run_wsl, bas.download, bas._set_creation = self.original
        self.reset_state()
        shutil.rmtree(bas.BASE, ignore_errors=True)
        config.update({"bac_a_sable": {"reseau_public": False}})
        bas.forget_cache()

    def state(self):
        return self.client.get("/api/bac-a-sable").json()

    def create(self, image="alpine"):
        return self.client.post("/api/bac-a-sable/creer", json={"image": image}, headers=ORIGIN)

    def wait_done(self):
        return wait_for(lambda: self.state()["creation"]["etat"] != "en cours" and self.state()["creation"])


class Etat(Base):
    def test_etat_initial(self):
        s = self.state()
        self.assertEqual((s["wsl"], s["cree"]), (True, False))
        self.assertEqual(s["creation"]["etat"], "inactif")
        self.assertEqual(s["image_par_defaut"], "debian")
        self.assertEqual(set(s["images"]), {"debian", "alpine"})
        self.assertFalse(s["reseau_public"])  # coupé par défaut

    def test_sans_wsl(self):
        self.fake.installed = False
        s = self.state()
        self.assertEqual((s["wsl"], s["cree"]), (False, False))


class Creation(Base):
    def test_image_inconnue(self):
        r = self.create("arch")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.state()["creation"]["etat"], "inactif")

    def test_sans_wsl_dit_quoi_faire(self):
        self.fake.installed = False
        r = self.create()
        self.assertEqual(r.status_code, 409)
        self.assertIn("wsl --install", r.json()["detail"])  # Pilote ne l'installe jamais lui-même

    def test_deja_cree(self):
        self.fake.distros.append(bas.DISTRO)
        r = self.create()
        self.assertEqual(r.status_code, 409)
        self.assertIn("existe déjà", r.json()["detail"])

    def test_creation_complete_avec_etapes_et_avancement(self):
        steps = []
        original = bas._set_creation

        def record(**changes):
            if "etape" in changes:
                steps.append(changes["etape"])
            original(**changes)

        bas._set_creation = record
        self.release.clear()  # retient le téléchargement : on voit l'état « en cours »
        r = self.create("alpine")
        self.assertEqual(r.status_code, 200)
        running = self.state()["creation"]
        self.assertEqual((running["etat"], running["image"], running["etape"]), ("en cours", "alpine", "telechargement"))
        wait_for(lambda: self.state()["creation"]["octets"] == 50)
        self.assertEqual(self.state()["creation"]["total"], 100)  # « 50 / 100 » pour la barre de progression
        self.release.set()
        done = self.wait_done()
        self.assertEqual(done["etat"], "ok")
        self.assertEqual(steps[:3], ["telechargement", "import", "preparation"])
        s = self.state()
        self.assertTrue(s["cree"])
        self.assertEqual(s["image"]["image"], "alpine")

    def test_une_seule_creation_a_la_fois(self):
        self.release.clear()
        self.assertEqual(self.create().status_code, 200)
        r = self.create("debian")
        self.assertEqual(r.status_code, 409)
        self.assertIn("déjà en cours", r.json()["detail"])
        deleted = self.client.post("/api/bac-a-sable/supprimer", json={"confirmer": True}, headers=ORIGIN)
        self.assertEqual(deleted.status_code, 409)  # pas de suppression pendant une création
        self.release.set()
        self.assertEqual(self.wait_done()["etat"], "ok")

    def test_echec_garde_son_message_puis_nouvel_essai(self):
        def failing(key, progress=None):
            raise bas.BacASableError("Image Alpine : le serveur a répondu 503.")

        working = bas.download
        bas.download = failing
        self.assertEqual(self.create().status_code, 200)
        failed = self.wait_done()
        self.assertEqual(failed["etat"], "erreur")
        self.assertIn("503", failed["message"])
        self.assertFalse(self.state()["cree"])  # rien de bancal : pas de distribution
        bas.download = working
        self.assertEqual(self.create().status_code, 200)  # on peut réessayer
        self.assertEqual(self.wait_done()["etat"], "ok")
        self.assertIsNone(self.state()["creation"]["message"])

    def test_erreur_inattendue_ne_bloque_pas_l_etat(self):
        def exploding(key, progress=None):
            raise RuntimeError("boum")

        bas.download = exploding
        self.assertEqual(self.create().status_code, 200)
        failed = self.wait_done()
        self.assertEqual(failed["etat"], "erreur")
        self.assertIn("Erreur inattendue : boum", failed["message"])  # jamais « en cours » pour toujours

    def test_une_autre_page_ne_peut_rien(self):
        for path, body in (("creer", {"image": "alpine"}), ("supprimer", {"confirmer": True})):
            r = self.client.post(f"/api/bac-a-sable/{path}", json=body, headers=FOREIGN)
            self.assertEqual(r.status_code, 403, path)
        self.assertEqual(self.state()["creation"]["etat"], "inactif")


class Suppression(Base):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.create().status_code, 200)
        self.wait_done()
        self.assertTrue(self.state()["cree"])

    def delete(self, **body):
        return self.client.post("/api/bac-a-sable/supprimer", json=body, headers=ORIGIN)

    def test_sans_confirmation_rien_n_est_supprime(self):
        for body in ({}, {"confirmer": False}):
            r = self.delete(**body)
            self.assertEqual(r.status_code, 400, body)
            self.assertIn("confirme", r.json()["detail"])
        self.assertTrue(self.state()["cree"])
        self.assertNotIn(["--unregister", bas.DISTRO], self.fake.calls)

    def test_confirmee(self):
        r = self.delete(confirmer=True)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["cree"])
        self.assertIn(["--unregister", bas.DISTRO], self.fake.calls)
        self.assertFalse(self.state()["cree"])


class Reglage(Base):
    def test_internet_public(self):
        r = self.client.put("/api/config", json={"bac_a_sable": {"reseau_public": True}}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["bac_a_sable"]["reseau_public"])
        self.assertTrue(self.state()["reseau_public"])
        self.assertTrue(bas.public_network())  # lu avant chaque commande de l'agent
        self.client.put("/api/config", json={"bac_a_sable": {"reseau_public": False}}, headers=ORIGIN)
        self.assertFalse(bas.public_network())

    def test_valeur_farfelue_refusee(self):
        r = self.client.put("/api/config", json={"bac_a_sable": {"reseau_public": "peut-être"}}, headers=ORIGIN)
        self.assertEqual(r.status_code, 422)
        self.assertFalse(bas.public_network())


class _Body(http.server.BaseHTTPRequestHandler):
    body = b"x" * 300_000

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *a):
        pass


class Progression(unittest.TestCase):
    def test_taille_totale_transmise_au_telechargement(self):
        import hashlib

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Body)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        original = dict(bas.IMAGES)
        self.addCleanup(lambda: (bas.IMAGES.clear(), bas.IMAGES.update(original)))
        self.addCleanup(shutil.rmtree, bas.BASE, ignore_errors=True)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        bas.IMAGES["essai"] = {"nom": "Essai", "url": f"http://127.0.0.1:{server.server_address[1]}/i.tar",
                               "sha256": hashlib.sha256(_Body.body).hexdigest(), "fichier": "essai.tar"}
        calls = []
        bas.download("essai", lambda done, total: calls.append((done, total)))
        self.assertTrue(calls)
        self.assertTrue(all(total == len(_Body.body) for _, total in calls))
        self.assertEqual(calls[-1][0], len(_Body.body))
        self.assertEqual([d for d, _ in calls], sorted(d for d, _ in calls))  # ne recule jamais


class Page(unittest.TestCase):
    """La page doit contenir tout ce que le script attend : un oubli (comme la liste blanche des sections) se voit ici."""

    @classmethod
    def setUpClass(cls):
        cls.html = (STATIC / "index.html").read_text(encoding="utf-8")
        cls.sandbox = (STATIC / "sandbox.js").read_text(encoding="utf-8")
        cls.settings = (STATIC / "settings.js").read_text(encoding="utf-8")
        cls.chat = (STATIC / "chat.js").read_text(encoding="utf-8")

    def test_section_connue_du_routeur_et_de_la_navigation(self):
        self.assertIn('<a href="#/reglages/bac-a-sable" data-section="bac-a-sable">', self.html)
        self.assertIn('<section class="settings-section" data-section="bac-a-sable"', self.html)
        sections = re.search(r"const SECTIONS = \[(.*?)\];", self.settings, re.S).group(1)
        self.assertIn('"bac-a-sable"', sections)  # sinon le routeur renvoie sur Général
        self.assertIn('"bac-a-sable": loadBacASable', self.settings)

    def test_chaque_identifiant_du_script_existe_dans_la_page(self):
        ids = set(re.findall(r'\$\("#([\w-]+)"\)', self.sandbox))
        self.assertTrue({"btn-sandbox", "bas-state", "btn-bas-create", "btn-bas-delete", "set-bas-public"} <= ids)
        for name in ids:
            self.assertRegex(self.html, rf'id="{re.escape(name)}"', name)

    def test_ordre_des_scripts(self):
        order = re.findall(r'<script src="([^"]+)"', self.html)
        self.assertLess(order.index("chat.js"), order.index("sandbox.js"))  # sandbox.js lit chat et state
        self.assertLess(order.index("sandbox.js"), order.index("settings.js"))  # settings.js nomme loadBacASable
        self.assertIn("sandboxSync", self.chat)  # la barre de l'agent la rappelle

    def test_la_confirmation_d_internet_public_n_est_demandee_qu_a_l_activation(self):
        block = self.sandbox[self.sandbox.index('$("#set-bas-public")'):]
        self.assertRegex(block, r"box\.checked && !confirm\(")
        self.assertIn("envoyer n'importe où le contenu de la copie", block)

    def test_la_suppression_exige_sa_confirmation_des_deux_cotes(self):
        self.assertIn("confirmer: true", self.sandbox)
        self.assertRegex(self.sandbox, r"if \(!confirm\(")


if __name__ == "__main__":
    unittest.main()
