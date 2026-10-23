"""Essai réel, avec un faux moteur : paliers inférieurs, restauration du modèle d'origine, arrêt, refus."""

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from base import GIB, ORIGIN, client

from aria_pilote import backups, trial
from aria_pilote.server import manager

ITEM = {"model_id": "qwen", "settings": {"ctx_size": 32768, "cache_type": "q8_0", "gpu_layers": 99, "flash_attn": "on"}}
ORIGINAL = {"ctx_size": 8192, "cache_type": "f16"}


class Trial(unittest.TestCase):
    def setUp(self):
        self.model = SimpleNamespace(id="qwen", key="qwen.gguf", name="Qwen", path="qwen.gguf", engine_auto="llama.cpp")
        self.calls, self.loads, self.free = [], [], int(2 * GIB)

        async def fake_load(model, settings, engine, auto):
            self.calls.append(("load", model.name, settings.get("ctx_size"), settings.get("cache_type")))
            manager.model, manager._settings, manager.engine, manager._auto = model, settings, engine, auto
            manager.state = "ready"

        async def fake_unload():
            self.calls.append(("unload",))
            manager.model, manager.state = None, "idle"

        async def trial_load(model, settings):
            await fake_load(model, settings, "llama.cpp", True)
            outcome = self.loads.pop(0)
            if outcome == "fail":
                manager.state = "error"
                return "le moteur s'est arrêté au chargement (mémoire insuffisante ?)"
            self.free = outcome
            return None

        async def fake_generate():
            return 46.2, 1450.0

        real_sleep = asyncio.sleep
        patches = [mock.patch.object(manager, "load", fake_load), mock.patch.object(manager, "unload", fake_unload),
                   mock.patch.object(manager, "model", None), mock.patch.object(manager, "state", "idle"),
                   mock.patch.object(manager, "_settings", None),
                   mock.patch.object(trial, "_load", trial_load), mock.patch.object(trial, "_generate", fake_generate),
                   mock.patch.object(trial, "_vram", lambda: (int(14 * GIB), self.free)),
                   mock.patch.object(trial.asyncio, "sleep", lambda d: real_sleep(0)),
                   mock.patch("aria_pilote.models.get", lambda mid: self.model if mid == "qwen" else None),
                   mock.patch("aria_pilote.models._read_meta", lambda p: {"general.architecture": "x", "x.block_count": 64})]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_trial(self, loads, items=(ITEM,), previous=False, stop_after=None):
        self.loads = list(loads)
        if previous:
            manager.model, manager.state, manager._settings = self.model, "ready", dict(ORIGINAL)
            manager.engine, manager._auto = "llama.cpp", True

        async def go():
            seen = []
            gen = trial.run(list(items))
            async for raw in gen:
                seen.append(json.loads(raw[6:]))
                if stop_after and len(seen) >= stop_after:
                    await gen.aclose()
                    break
            for _ in range(5):  # la restauration tourne dans une tâche à part
                await asyncio.sleep(0)
            return seen
        evs = asyncio.run(go())
        return evs, next((e for e in evs if e["type"] == "trial_result"), None)

    def test_reussite_directe_puis_restauration(self):
        evs, res = self.run_trial([int(2 * GIB)], previous=True)
        self.assertTrue(res["ok"])
        self.assertFalse(res["adjusted"])
        self.assertEqual(res["tok_s"], 46.2)
        self.assertEqual(self.calls[-1], ("load", "Qwen", 8192, "f16"))  # modèle d'origine, anciens réglages
        self.assertEqual(evs[-1], {"type": "trial_done", "restored": "Qwen"})

    def test_echec_puis_palier_inferieur(self):
        evs, res = self.run_trial(["fail", int(2 * GIB)])
        self.assertTrue(res["ok"] and res["adjusted"])
        self.assertEqual(res["settings"]["ctx_size"], 16384)
        self.assertTrue(any("Palier inférieur" in e.get("message", "") for e in evs))
        self.assertEqual(self.calls[-1], ("unload",))  # aucun modèle d'origine : moteur libéré

    def test_vram_trop_juste(self):
        evs, res = self.run_trial([int(0.2 * GIB), int(1.5 * GIB)])
        self.assertTrue(res["ok"] and res["adjusted"])
        self.assertTrue(any("0.2 Go" in e.get("message", "") for e in evs))

    def test_ne_tient_jamais(self):
        _, res = self.run_trial(["fail"] * 10)
        self.assertFalse(res["ok"])
        self.assertIn("essais", res["message"])

    def test_arret_en_cours_de_route(self):
        self.run_trial([int(2 * GIB)] * 5, items=(ITEM, ITEM), previous=True, stop_after=2)
        self.assertEqual(self.calls[-1], ("load", "Qwen", 8192, "f16"))
        self.assertFalse(trial.is_running())


class TrialApi(unittest.TestCase):
    def setUp(self):
        self.api = client()
        self.body = {"items": [ITEM]}

    def test_refus_pendant_une_tache_de_l_agent(self):
        backups.set_running("abcdefabcdef", "abcdefabcdef")
        self.addCleanup(backups.set_running, "abcdefabcdef", None)
        self.assertEqual(self.api.post("/api/optimize/test", json=self.body, headers=ORIGIN).status_code, 409)

    def test_refus_si_essai_deja_en_cours(self):
        with mock.patch.object(trial, "_running", True):
            self.assertEqual(self.api.post("/api/optimize/test", json=self.body, headers=ORIGIN).status_code, 409)

    def test_refus_sans_modele(self):
        self.assertEqual(self.api.post("/api/optimize/test", json={"items": []}, headers=ORIGIN).status_code, 400)


if __name__ == "__main__":
    unittest.main()
