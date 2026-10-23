"""Page Réglages : modèle par défaut, chargement au démarrage, règles de tous les projets."""

import asyncio
import copy
import unittest
from unittest import mock

from base import HOME, ORIGIN, client, fake_gguf, workspace

from aria_pilote import config, main, models, permissions
from aria_pilote.server import manager


def keep_config(test):
    saved = copy.deepcopy(config.get())

    def restore():
        with config._lock:
            config._data = saved
            config._save_locked()
        models.scan(force=True)
    test.addCleanup(restore)


class DefaultModel(unittest.TestCase):
    def setUp(self):
        keep_config(self)
        self.dir = HOME / "modeles-reglages"
        fake_gguf(self.dir)
        config.update({"model_dirs": [str(self.dir)]})
        self.model = models.scan(force=True)[0]

    def test_enregistre_par_l_api(self):
        api = client()
        cfg = api.put("/api/config", json={"default_model": self.model.id, "autoload": True}, headers=ORIGIN).json()
        self.assertEqual((cfg["default_model"], cfg["autoload"]), (self.model.id, True))
        cfg = api.put("/api/config", json={"default_model": ""}, headers=ORIGIN).json()
        self.assertEqual((cfg["default_model"], cfg["autoload"]), ("", True))  # le reste n'a pas bougé

    def autoload(self):
        with mock.patch.object(manager, "load", mock.AsyncMock()) as load:
            asyncio.run(main._autoload())
        return load

    def test_charge_au_demarrage(self):
        config.update({"default_model": self.model.id, "autoload": True})
        load = self.autoload()
        load.assert_awaited_once()
        self.assertEqual(load.await_args.args[0].id, self.model.id)

    def test_rien_sans_l_option(self):
        config.update({"default_model": self.model.id, "autoload": False})
        self.autoload().assert_not_awaited()

    def test_modele_disparu_sans_erreur(self):
        config.update({"default_model": "000000000000", "autoload": True})
        self.autoload().assert_not_awaited()


class AllRules(unittest.TestCase):
    def setUp(self):
        keep_config(self)
        self.a, self.b = workspace("projet-a"), workspace("projet-b")
        permissions.add(self.a, "git status*", "git status")
        permissions.add(self.b, "python -m unittest*", "python -m unittest")

    def test_liste_et_retrait(self):
        api = client()
        projects = {p["root"]: p["rules"] for p in api.get("/api/rules").json()}
        self.assertEqual(projects[str(self.a.resolve()).lower()], ["git status*"])
        self.assertEqual(projects[str(self.b.resolve()).lower()], ["python -m unittest*"])
        after = api.request("DELETE", "/api/rules", json={"root": str(self.a.resolve()).lower(), "rule": "git status*"},
                            headers=ORIGIN).json()
        self.assertNotIn(str(self.a.resolve()).lower(), {p["root"] for p in after})  # projet vide : plus listé
        self.assertEqual(permissions.rules(self.b), ["python -m unittest*"])  # l'autre projet n'a pas bougé
        self.assertEqual(api.request("DELETE", "/api/rules", json={"root": str(self.a), "rule": "x"},
                                     headers=ORIGIN).status_code, 404)


if __name__ == "__main__":
    unittest.main()
