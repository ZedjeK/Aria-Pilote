"""Bac à sable, outils de développement : installation à la création (par image, avec échec propre), option, règle donnée
à l'agent, listage élagué, et les tâches 24 et 25 du banc (environnement et vérificateurs). Faux wsl.exe pour l'essentiel ;
le vrai essai ne tourne que sur demande (ARIA_TEST_WSL=1) : il crée puis supprime une vraie distribution.
"""

import importlib.util
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from base import HOME, ORIGIN, SRC, workspace
from test_bac_a_sable import FakeWsl, image_tar
from test_bac_a_sable_interface import Base as ApiBase

from aria_pilote import bac_a_sable as bas, bac_retour, config
from aria_pilote import agent

sys.path.insert(0, str(SRC / "tools"))
sys.path.insert(0, str(SRC / "bench"))
import bench  # noqa: E402

TASKS = SRC / "bench" / "tasks"


class FakeTools(FakeWsl):
    """Le faux wsl.exe, avec l'installation des outils qui peut échouer."""

    def __init__(self):
        super().__init__()
        self.tools_code = 0

    def as_root(self, rest, stdin):
        if rest[:3] == ["--exec", "sh", "-c"] and rest[3] in {s["outils"] for s in bas.IMAGES.values()}:
            return self.tools_code, "" if self.tools_code == 0 else "E: Unable to locate package python3"
        return super().as_root(rest, stdin)


class Installation(unittest.TestCase):
    def setUp(self):
        self.fake = FakeTools()
        self.original = bas.run_wsl, bas.download
        bas.run_wsl = self.fake
        self.image = image_tar(HOME / "image-outils.tar.gz")
        bas.download = lambda key, progress=None: self.image
        self.steps = []
        self.addCleanup(self.restore)

    def restore(self):
        bas.run_wsl, bas.download = self.original
        shutil.rmtree(bas.BASE, ignore_errors=True)
        bas.forget_cache()

    def create(self, key="alpine", **kw):
        return bas.create(key, step=self.steps.append, **kw)

    def root_calls(self):
        return [c for c in self.fake.calls if c[:4] == ["-d", bas.DISTRO, "-u", "root"]]

    def test_par_defaut_root_installe_apres_la_preparation_et_avant_le_redemarrage(self):
        state = self.create("alpine")
        install = ["-d", bas.DISTRO, "-u", "root", "--exec", "sh", "-c", bas.IMAGES["alpine"]["outils"]]
        setup = ["-d", bas.DISTRO, "-u", "root", "--exec", "sh", "-c", bas.SETUP]
        calls = self.fake.calls
        self.assertLess(calls.index(setup), calls.index(install))
        self.assertLess(calls.index(install), calls.index(["--terminate", bas.DISTRO]))
        self.assertEqual(self.steps, ["telechargement", "import", "preparation", "outils"])
        self.assertTrue(state["image"]["outils"])
        self.assertTrue(bas.tools_installed())
        self.assertEqual(state["outils"], bas.TOOLS_SUMMARY)

    def test_sans_outils(self):
        state = self.create("alpine", outils=False)
        self.assertFalse(any(bas.IMAGES["alpine"]["outils"] in c for c in self.fake.calls))
        self.assertEqual(self.steps, ["telechargement", "import", "preparation"])
        self.assertFalse(state["image"]["outils"])
        self.assertFalse(bas.tools_installed())

    def test_installation_ratee_rien_de_bancal(self):
        self.fake.tools_code = 100
        with self.assertRaises(bas.BacASableError) as ctx:
            self.create("debian")
        message = str(ctx.exception)
        self.assertIn("Unable to locate package", message)
        self.assertIn("sans les outils de développement", message)  # la sortie de secours est dite
        self.assertIn(["--unregister", bas.DISTRO], self.fake.calls)  # jamais un bac à moitié fait
        self.assertFalse(bas.status()["cree"])
        self.assertFalse(bas.STATE_FILE.exists())
        self.assertFalse(bas.tools_installed())

    def test_commandes_par_image(self):
        for key, spec in bas.IMAGES.items():
            command = spec["outils"]
            for wanted in ("python3", "git", "curl", "make", "ca-certificates"):
                self.assertIn(wanted, command, (key, wanted))
            self.assertNotIn("sudo", command)
        self.assertIn("python3-venv", bas.IMAGES["debian"]["outils"])  # Debian sépare venv de python3
        self.assertIn("--no-install-recommends", bas.IMAGES["debian"]["outils"])
        self.assertIn("DEBIAN_FRONTEND=noninteractive", bas.IMAGES["debian"]["outils"])
        self.assertTrue(bas.IMAGES["alpine"]["outils"].startswith("apk add --no-cache"))
        self.assertNotIn("apt", bas.IMAGES["alpine"]["outils"])


class ParLApi(ApiBase):
    def test_option_transmise_a_la_creation(self):
        r = self.client.post("/api/bac-a-sable/creer", json={"image": "alpine", "outils": False}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(self.state()["creation"]["outils"])
        self.wait_done()
        self.assertFalse(self.state()["image"]["outils"])

    def test_outils_par_defaut(self):
        r = self.create()
        self.assertEqual(r.status_code, 200)
        self.assertTrue(self.state()["creation"]["outils"])
        self.wait_done()
        self.assertTrue(self.state()["image"]["outils"])
        self.assertIn("Python 3", self.state()["outils"])  # de quoi le dire dans l'interface

    def test_etape_outils_annoncee(self):
        seen = []
        original = bas._set_creation

        def record(**changes):
            if "etape" in changes:
                seen.append(changes["etape"])
            original(**changes)

        bas._set_creation = record
        self.create()
        self.wait_done()
        self.assertEqual(seen[:4], ["telechargement", "import", "preparation", "outils"])


class Regle(unittest.TestCase):
    def setUp(self):
        self.addCleanup(shutil.rmtree, bas.BASE, ignore_errors=True)
        self.addCleanup(config.update, {"bac_a_sable": {"reseau_public": False}})

    def rule(self, tools, public):
        bas.BASE.mkdir(parents=True, exist_ok=True)
        bas.STATE_FILE.write_text('{"outils": %s}' % ("true" if tools else "false"), encoding="utf-8")
        config.update({"bac_a_sable": {"reseau_public": public}})
        return agent.sandbox_rule()

    def test_avec_outils_sans_internet(self):
        text = self.rule(True, False)
        self.assertIn("Présents : python3 avec pip et venv, git, curl, make", text)
        self.assertIn("python3 -m venv .venv", text)  # Debian refuse pip hors d'un venv
        self.assertIn("seule la bibliothèque standard est utilisable", text)
        self.assertIn("Il n'a pas de réseau", text)

    def test_avec_outils_et_internet(self):
        text = self.rule(True, True)
        self.assertIn("L'Internet public est ouvert (pip install, git clone, curl), pas le réseau local", text)
        self.assertIn("apt ne t'est pas ouvert", text)  # plus de promesse que le code ne tient pas
        self.assertNotIn("(pip, apt)", text)

    def test_sans_outils(self):
        text = self.rule(False, False)
        self.assertIn("Seul le shell de base est présent : ni Python, ni git, ni curl, ni make", text)
        self.assertNotIn("Présents :", text)

    def test_outils_de_fichiers_sur_le_vrai_projet(self):
        # Mesuré au banc : sans cette phrase, le modèle éditait le vrai projet avec edit_file puis lançait ses tests
        # dans une copie restée intacte
        for tools in (True, False):
            text = " ".join(self.rule(tools, False).split())
            self.assertIn("edit_file, write_file et les autres outils de fichiers travaillent sur le VRAI projet", text)
            self.assertIn("pour changer un fichier de la copie, passe par run_sandbox", text)

    def test_aucun_retour_chariot_dans_le_prompt(self):
        self.assertNotIn("\r", self.rule(True, True))


class Listage(unittest.TestCase):
    def test_dossiers_elagues(self):
        for name in (".venv", "venv", "node_modules", "__pycache__", ".git", ".pytest_cache"):
            self.assertIn(name, bas.PRUNED_DIRS)
            self.assertIn(f"-name {name}", bas.LISTING_SCRIPT)
        self.assertEqual(bas.LISTING_SCRIPT.count("-prune"), 2)  # empreintes, puis tailles
        self.assertIs(bac_retour.IGNORED_DIRS, bas.PRUNED_DIRS)  # une seule définition de ce qu'on ignore
        self.assertTrue(bas.SKIP_DIRS <= bas.PRUNED_DIRS)


# --- banc d'essai ------------------------------------------------------------------------------------------------

class _Reponse:
    def __init__(self, data=None):
        self.data = data

    def json(self):
        return self.data

    def raise_for_status(self):
        return None


class FauxBanc:
    """Le client du banc d'essai, réduit à l'API du bac à sable."""

    def __init__(self, wsl=True, cree=False, outils=True, final="ok"):
        self.wsl, self.cree, self.outils, self.final = wsl, cree, outils, final
        self.posts, self.creating, self.polls = [], False, 0

    def get(self, url):
        assert url == "/api/bac-a-sable"
        creation = {"etat": "inactif", "message": None}
        if self.creating:
            self.polls += 1
            creation = {"etat": "en cours" if self.polls < 3 else self.final,
                        "message": "pas de réseau" if self.final == "erreur" else None}
            if creation["etat"] == "ok":
                self.cree, self.outils = True, True
        return _Reponse({"wsl": self.wsl, "cree": self.cree, "creation": creation,
                         "image": {"outils": self.outils} if self.cree else None})

    def post(self, url, json=None, headers=None):
        self.posts.append((url, json))
        if url.endswith("/creer"):
            self.creating = True
        if url.endswith("/supprimer"):
            self.cree = False
        return _Reponse()


class Banc(unittest.TestCase):
    def environment(self, client, tasks):
        with mock.patch.object(bench.time, "sleep"):
            with bench.sandbox_environment(client, tasks):
                pass

    def test_les_taches_24_et_25_activent_le_bac(self):
        tasks = bench.load_tasks({24, 25})
        self.assertEqual([t["name"] for t in tasks], ["24-bac-a-sable-corriger", "25-bac-a-sable-spontane"])
        self.assertTrue(all(t["sandbox"] for t in tasks))
        self.assertIn("sandbox_apply", tasks[0]["prompt"])  # la 24 guide le modèle
        self.assertNotIn("bac", tasks[1]["prompt"])  # la 25 ne lui dit rien : elle mesure son choix

    def test_aucune_tache_n_en_a_besoin(self):
        client = FauxBanc()
        self.environment(client, [{"name": "x"}])
        self.assertEqual(client.posts, [])

    def test_cree_puis_supprime_ce_qu_il_a_cree(self):
        client = FauxBanc(cree=False)
        self.environment(client, [{"sandbox": True}])
        self.assertEqual([url for url, _ in client.posts], ["/api/bac-a-sable/creer", "/api/bac-a-sable/supprimer"])
        self.assertEqual(client.posts[0][1], {"image": "alpine", "outils": True})  # avec ses outils
        self.assertEqual(client.posts[1][1], {"confirmer": True})
        self.assertFalse(client.cree)

    def test_un_bac_deja_la_n_est_jamais_supprime(self):
        client = FauxBanc(cree=True, outils=True)
        self.environment(client, [{"sandbox": True}])
        self.assertEqual(client.posts, [])

    def test_bac_sans_outils_refuse(self):
        client = FauxBanc(cree=True, outils=False)
        with self.assertRaises(SystemExit) as ctx:
            self.environment(client, [{"sandbox": True}])
        self.assertIn("supprime-le", str(ctx.exception))
        self.assertEqual(client.posts, [])  # il ne touche pas à ce qui n'est pas à lui

    def test_sans_wsl(self):
        with self.assertRaises(SystemExit) as ctx:
            self.environment(FauxBanc(wsl=False), [{"sandbox": True}])
        self.assertIn("WSL 2", str(ctx.exception))

    def test_creation_ratee(self):
        client = FauxBanc(final="erreur")
        with self.assertRaises(SystemExit) as ctx:
            self.environment(client, [{"sandbox": True}])
        self.assertIn("pas de réseau", str(ctx.exception))


def load_check(name):
    spec = importlib.util.spec_from_file_location(f"check_{name}", TASKS / name / "check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.check


class Verificateurs(unittest.TestCase):
    def project(self, fixed=False, tests_changed=False):
        ws = Path(tempfile.mkdtemp(dir=HOME))
        shutil.copytree(TASKS / "24-bac-a-sable-corriger" / "fixture", ws, dirs_exist_ok=True)
        start = Path(tempfile.mkdtemp(dir=HOME))
        shutil.copytree(ws, start, dirs_exist_ok=True)
        if fixed:
            code = (ws / "panier.py").read_text(encoding="utf-8").replace("total_ht -= remise", "total_ht *= 1 - remise")
            (ws / "panier.py").write_text(code, encoding="utf-8")
        if tests_changed:
            (ws / "test_panier.py").write_text("import unittest\n", encoding="utf-8")
        self.addCleanup(shutil.rmtree, ws, ignore_errors=True)
        self.addCleanup(shutil.rmtree, start, ignore_errors=True)
        return ws, {"calls": [], "start": start}

    def run_with(self, run, *tools):
        run["calls"] = [{"name": t} for t in tools]
        return run

    def test_le_projet_de_depart_echoue(self):
        ws, run = self.project()
        ok, reason = load_check("24-bac-a-sable-corriger")(ws, self.run_with(run, "run_sandbox", "sandbox_apply"))
        self.assertFalse(ok)
        self.assertIn("échouent encore", reason)

    def test_24_guidee(self):
        check = load_check("24-bac-a-sable-corriger")
        ws, run = self.project(fixed=True)
        self.assertEqual(check(ws, self.run_with(run, "run_sandbox", "sandbox_diff", "sandbox_apply")),
                         (True, "corrigée dans le bac, rapportée avec sandbox_apply"))
        # Corrigé directement dans le projet : la tâche demandait le bac
        ok, reason = check(ws, self.run_with(run, "run_sandbox", "edit_file"))
        self.assertFalse(ok)
        self.assertIn("edit_file utilisé", reason)
        ok, reason = check(ws, self.run_with(run, "read_file"))
        self.assertEqual((ok, reason), (False, "le bac à sable n'a pas servi"))
        ok, reason = check(ws, self.run_with(run, "run_sandbox"))
        self.assertIn("sandbox_apply", reason)  # essayé dans le bac mais jamais rapporté
        self.assertFalse(ok)

    def test_25_spontanee_reussit_par_les_trois_chemins(self):
        check = load_check("25-bac-a-sable-spontane")
        ws, run = self.project(fixed=True)
        self.assertIn("rapporté avec sandbox_apply", check(ws, self.run_with(run, "run_sandbox", "sandbox_apply"))[1])
        self.assertIn("corrigé directement dans le projet", check(ws, self.run_with(run, "run_sandbox", "edit_file"))[1])
        ok, reason = check(ws, self.run_with(run, "edit_file"))
        self.assertTrue(ok)
        self.assertIn("sans le bac à sable", reason)  # la mesure : il ne l'a pas choisi

    def test_le_test_modifie_ou_non_corrige_echoue_toujours(self):
        for name in ("24-bac-a-sable-corriger", "25-bac-a-sable-spontane"):
            check = load_check(name)
            ws, run = self.project(fixed=True, tests_changed=True)
            self.assertEqual(check(ws, self.run_with(run, "run_sandbox", "sandbox_apply")),
                             (False, "le test a été modifié"), name)
            ws, run = self.project()
            self.assertFalse(check(ws, self.run_with(run, "run_sandbox", "sandbox_apply"))[0], name)


@unittest.skipUnless(sys.platform == "win32" and os.environ.get("ARIA_TEST_WSL") == "1",
                     "vrai WSL : ARIA_TEST_WSL=1 (crée puis supprime une distribution d'essai)")
class VraiWsl(unittest.TestCase):
    def setUp(self):
        self.original = bas.DISTRO
        bas.DISTRO = "aria-bac-a-sable-test"
        self.addCleanup(self.restore)

    def restore(self):
        bas.delete()
        bas.DISTRO = self.original
        bas.forget_cache()

    def test_les_tests_d_un_projet_python_tournent_dans_le_bac(self):
        from aria_pilote import tools

        root = workspace("outils")
        shutil.copytree(TASKS / "24-bac-a-sable-corriger" / "fixture", root, dirs_exist_ok=True)
        cid = "0123456789ab"
        bas.create("alpine")  # avec ses outils : c'est le défaut
        self.assertTrue(bas.tools_installed())
        bas.copy_project(cid, root)
        # Python, git, curl et make sont là, pour l'agent
        out = bas.run_in_project(cid, "python3 --version && git --version && curl --version | head -1 && make --version | head -1")
        self.assertEqual(out["code"], 0, out)
        # Les tests échouent comme prévu, puis passent une fois corrigés
        failing = bas.run_in_project(cid, "python3 -m unittest -q 2>&1 | tail -3")
        self.assertIn("FAILED (failures=1)", failing["sortie"])
        fixed = bas.run_in_project(cid, "sed -i 's/total_ht -= remise/total_ht *= 1 - remise/' panier.py && "
                                        "python3 -m unittest -q 2>&1 | tail -3")
        self.assertIn("OK", fixed["sortie"])
        # Sans Internet public, pip ne télécharge rien ; avec lui, un venv fonctionne
        venv = bas.run_in_project(cid, "python3 -m venv .venv && . .venv/bin/activate && pip --version | cut -c1-12")
        self.assertEqual(venv["code"], 0, venv)
        offline = bas.run_in_project(cid, ". .venv/bin/activate && pip install --disable-pip-version-check six 2>&1 | tail -1")
        self.assertIn("No matching distribution", offline["sortie"])
        # Un .venv de milliers de fichiers ne ralentit pas le listage et n'est jamais rapporté
        bas.run_in_project(cid, "mkdir -p node_modules/x && echo 1 > node_modules/x/i.js && echo 1 > .venv/lib/ajout.txt")
        listing = bas.sandbox_listing(cid)
        self.assertFalse(any(name.startswith((".venv/", "node_modules/")) for name in listing), sorted(listing))
        found = tools._sandbox_changes(cid, str(root), None)
        self.assertEqual([f["rel"] for f in found["files"]], ["panier.py"])  # la correction, rien d'autre
        self.assertEqual(found["scan"]["ajoutes"], [])


if __name__ == "__main__":
    unittest.main()
