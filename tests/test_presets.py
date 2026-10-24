"""Presets : preset par défaut des nouvelles conversations, repli après suppression, bornes des valeurs."""

import copy
import json
import unittest

from base import ORIGIN, client

from aria_pilote import config, presets


def keep_state(test):
    saved_config = copy.deepcopy(config.get())
    saved_presets = copy.deepcopy(presets.list_all())

    def restore():
        with config._lock:
            config._data = saved_config
            config._save_locked()
        with presets._lock:
            presets._write(saved_presets)
    test.addCleanup(restore)


class Presets(unittest.TestCase):
    def setUp(self):
        keep_state(self)
        self.api = client()

    def create(self, name, **values):
        resp = self.api.post("/api/presets", json={"name": name, **values}, headers=ORIGIN)
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def test_preset_par_defaut_des_nouvelles_conversations(self):
        rapide = self.create("Rapide", temperature=0.5)
        self.assertEqual(self.api.put("/api/config", json={"default_preset": rapide["id"]}, headers=ORIGIN).status_code, 200)
        conv = self.api.post("/api/conversations", json={}, headers=ORIGIN).json()
        self.assertEqual(conv["preset_id"], rapide["id"])
        self.assertEqual(self.api.put("/api/config", json={"default_preset": "inconnu"}, headers=ORIGIN).status_code, 400)

    def test_repli_quand_le_preset_est_supprime(self):
        rapide = self.create("Rapide")
        config.update({"default_preset": "default"})
        self.assertEqual(presets.resolve(rapide["id"])["id"], rapide["id"])
        self.api.delete(f"/api/presets/{rapide['id']}", headers=ORIGIN)
        self.assertEqual(presets.resolve(rapide["id"])["id"], "default")  # la conversation repasse sur le défaut

    def test_supprimer_le_preset_par_defaut(self):
        rapide = self.create("Rapide")
        config.update({"default_preset": rapide["id"]})
        self.api.delete(f"/api/presets/{rapide['id']}", headers=ORIGIN)
        self.assertEqual(config.get()["default_preset"], presets.list_all()[0]["id"])

    def test_bornes_des_valeurs(self):
        for bad in ({"temperature": 3}, {"top_p": 1.5}, {"min_p": -0.1}, {"max_tokens": 0}, {"presence_penalty": 5}):
            resp = self.api.post("/api/presets", json={"name": "x", **bad}, headers=ORIGIN)
            self.assertEqual(resp.status_code, 422, json.dumps(bad))
        self.assertEqual(self.api.post("/api/presets", json={"name": ""}, headers=ORIGIN).status_code, 422)
        self.assertEqual(self.create("Ok", temperature=2, top_p=1, thinking=None)["temperature"], 2)


if __name__ == "__main__":
    unittest.main()
