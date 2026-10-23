"""Recherche de mises à jour (GitHub simulé : jamais de vraie requête) et réglages des notifications."""

import asyncio
import copy
import time
import unittest
from unittest import mock

import httpx
from base import ORIGIN, client

from aria_pilote import config, updates


def keep_state(test):
    saved_config = copy.deepcopy(config.get())
    saved_cache = dict(updates._cache)

    def restore():
        with config._lock:
            config._data = saved_config
            config._save_locked()
        updates._cache.update(saved_cache)
    test.addCleanup(restore)
    updates._cache.update({"checked_at": None, "latest": None, "error": None})


class Versions(unittest.TestCase):
    def test_plus_grand_tag(self):
        self.assertEqual(updates.latest_tag(["v0.9.0", "v0.12.0", "v0.11.3", "nightly", "v1.0-rc1"]), "0.12.0")
        self.assertEqual(updates.latest_tag(["v0.10.0", "v0.9.9"]), "0.10.0")  # numérique, pas alphabétique
        self.assertIsNone(updates.latest_tag([]))
        self.assertIsNone(updates.latest_tag(["test", "release"]))


class Check(unittest.TestCase):
    def setUp(self):
        keep_state(self)

    def run_check(self, tags=None, error=None, force=False, version="0.11.0"):
        fetch = mock.AsyncMock(return_value=tags, side_effect=error)
        with mock.patch.object(updates, "_fetch_tags", fetch), mock.patch.object(updates, "VERSION", version):
            result = asyncio.run(updates.check(force=force))
        return result, fetch

    def test_nouvelle_version(self):
        u, _ = self.run_check(["v0.11.0", "v0.12.0"])
        self.assertTrue(u["newer"])
        self.assertEqual(u["latest"], "0.12.0")
        self.assertEqual(u["changes_url"], "https://github.com/ZedjeK/Aria-Pilote/compare/v0.11.0...v0.12.0")

    def test_a_jour_et_version_de_dev(self):
        u, _ = self.run_check(["v0.11.0"])
        self.assertEqual((u["newer"], u["dev"]), (False, False))
        u, _ = self.run_check(["v0.11.0"], force=True, version="0.12.0")
        self.assertEqual((u["newer"], u["dev"]), (False, True))

    def test_une_requete_par_jour_au_plus(self):
        _, fetch = self.run_check(["v0.12.0"])
        fetch.assert_awaited_once()
        _, fetch = self.run_check(["v0.13.0"])
        fetch.assert_not_awaited()  # réponse de moins de 24 h : gardée
        _, fetch = self.run_check(["v0.13.0"], force=True)
        fetch.assert_awaited_once()  # « Vérifier maintenant »
        updates._cache["checked_at"] = time.time() - updates.MAX_AGE_S - 1
        _, fetch = self.run_check(["v0.13.0"])
        fetch.assert_awaited_once()

    def test_hors_ligne(self):
        u, _ = self.run_check(error=httpx.ConnectError("pas de réseau"))
        self.assertEqual(u["error"], "Impossible de joindre GitHub.")
        self.assertFalse(u["newer"])
        u, _ = self.run_check(error=RuntimeError("GitHub limite les requêtes pour l'instant : réessaie plus tard."), force=True)
        self.assertIn("limite", u["error"])


class Api(unittest.TestCase):
    def setUp(self):
        keep_state(self)
        self.api = client()

    def test_recherche_desactivee_sans_requete(self):
        config.update({"update_check": False})
        with mock.patch.object(updates, "_fetch_tags", mock.AsyncMock(return_value=["v9.9.9"])) as fetch:
            u = self.api.get("/api/update").json()
            fetch.assert_not_awaited()  # rien n'est envoyé à GitHub
            self.assertIsNone(u["checked_at"])
            u = self.api.post("/api/update/check", headers=ORIGIN).json()  # sauf si tu le demandes
            fetch.assert_awaited_once()
        self.assertTrue(u["newer"])

    def test_reglages_des_notifications(self):
        cfg = self.api.put("/api/config", json={"notifications": {"enabled": True, "model": False}}, headers=ORIGIN).json()
        n = cfg["notifications"]
        self.assertEqual((n["enabled"], n["model"], n["task"], n["only_background"]), (True, False, True, True))
        self.assertEqual(self.api.put("/api/config", json={"notifications": {"enabled": "peut-être"}},
                                      headers=ORIGIN).status_code, 422)


if __name__ == "__main__":
    unittest.main()
