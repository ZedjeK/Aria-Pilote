"""Optimiseur : vrais en-têtes de Qwen 3.8 / Bonsai 2 / SmolLM, trois choix, cas limites, arguments de llama-server.

Les budgets de VRAM reprennent les chargements réels mesurés sur une RTX 4080 SUPER (13,5 Go libres)."""

import unittest
from types import SimpleNamespace
from unittest import mock

from base import GIB, model_data

from aria_pilote import config, optimizer, server

BUDGET = int(13.5 * GIB)


class Optimizer(unittest.TestCase):
    def setUp(self):
        # Projecteur vision : sa taille, sans fichier de 600 Mo
        patcher = mock.patch.object(optimizer, "_mmproj_bytes",
                                    lambda m, s: getattr(m, "mmproj_size", 0) if s.get("use_mmproj") else 0)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.qwen, self.qmeta = model_data("qwen")
        self.bonsai, self.bmeta = model_data("bonsai")
        self.base = dict(config.DEFAULTS["load_defaults"], use_mmproj=True)

    def proposed(self, model, meta, profile, budget=BUDGET, settings=None):
        return optimizer.plan(model, meta, settings or self.base, budget, profile, 12)

    def test_architecture_hybride_une_couche_d_attention_sur_quatre(self):
        self.assertEqual(optimizer.kv_values_per_token(self.qmeta) * 2, 64 * 1024)  # 64 Kio par token

    def test_trois_choix_avec_les_vrais_modeles(self):
        expected = {("qwen", "rapide"): (16384, "f16"), ("qwen", "compromis"): (32768, "q8_0"),
                    ("bonsai", "rapide"): (16384, "f16"), ("bonsai", "compromis"): (32768, "f16"),
                    ("bonsai", "contexte"): (131072, "q8_0")}
        for (name, profile), (ctx, cache) in expected.items():
            model, meta = (self.qwen, self.qmeta) if name == "qwen" else (self.bonsai, self.bmeta)
            with self.subTest(modele=name, choix=profile):
                p = self.proposed(model, meta, profile)["proposed"]
                self.assertEqual((p["ctx_size"], p["cache_type"], p["gpu_layers"]), (ctx, cache, 99))
                if cache == "q8_0":
                    self.assertEqual(p["flash_attn"], "on")

    def test_estimation_proche_de_la_mesure_reelle(self):
        estimate = self.proposed(self.bonsai, self.bmeta, "compromis")["vram_estimate"]
        self.assertGreaterEqual(estimate, 10.24 * GIB * 0.95)  # mesuré : 10,24 Go
        loaded = optimizer.footprint(self.qwen, self.qmeta, dict(self.base, ctx_size=16384, gpu_layers=99, cache_type="f16"))
        self.assertLess(abs(loaded / GIB - 12.65), 0.8)  # mesuré : 12,65 Go

    def test_le_plus_rapide_ne_compresse_jamais(self):
        p = self.proposed(self.qwen, self.qmeta, "rapide", budget=int(12.5 * GIB))["proposed"]
        self.assertEqual(p["cache_type"], "f16")
        self.assertLessEqual(p["ctx_size"], 16384)

    def test_vram_trop_petite_dechargement_partiel(self):
        p = self.proposed(self.qwen, self.qmeta, "compromis", budget=8 * GIB)
        self.assertTrue(0 < p["proposed"]["gpu_layers"] < 65)
        self.assertEqual(p["proposed"]["threads"], 12)
        self.assertIn("ne tient pas entier", p["notes"][0])

    def test_sans_carte_nvidia(self):
        p = self.proposed(self.qwen, self.qmeta, "compromis", budget=None)
        self.assertEqual(p["proposed"]["gpu_layers"], 0)
        self.assertIn("processeur", p["notes"][0])

    def test_entete_incomplet(self):
        p = self.proposed(self.qwen, {}, "compromis")
        self.assertFalse(p["changed"])
        self.assertIn("incomplet", p["notes"][0])

    def test_contexte_jamais_au_dela_de_celui_du_modele(self):
        small, meta = model_data("smollm2")
        self.assertEqual(self.proposed(small, meta, "contexte")["proposed"]["ctx_size"], small.context_length)

    def test_deja_optimal(self):
        current = dict(self.base, ctx_size=32768, gpu_layers=99, cache_type="q8_0", flash_attn="on", threads=0)
        self.assertFalse(self.proposed(self.qwen, self.qmeta, "compromis", settings=current)["changed"])

    def test_paliers_de_l_essai_reel(self):
        settings, seen = {"ctx_size": 131072, "cache_type": "f16", "gpu_layers": 99, "flash_attn": "auto"}, []
        while settings is not None and len(seen) < 8:
            seen.append(f"{settings['ctx_size'] // 1024}k/{settings['cache_type']}/{settings['gpu_layers']}")
            settings = optimizer.step_down(settings, 64)
        self.assertEqual(seen[:7], ["128k/f16/99", "128k/q8_0/99", "64k/q8_0/99", "32k/q8_0/99", "16k/q8_0/99",
                                    "8k/q8_0/99", "8k/q8_0/56"])
        self.assertIsNone(optimizer.step_down({"ctx_size": 8192, "cache_type": "q8_0", "gpu_layers": 0}, 64))


class LlamaServerArgs(unittest.TestCase):
    def setUp(self):
        self.model = SimpleNamespace(path="m.gguf", name="m", mmproj=None)
        self.base = dict(config.DEFAULTS["load_defaults"])

    def test_cache_compresse_force_la_flash_attention(self):
        args = server.build_args(self.model, dict(self.base, cache_type="q8_0", flash_attn="auto"), 8701, 8700)
        self.assertEqual(args[args.index("-ctk") + 1], "q8_0")
        self.assertEqual(args[args.index("-ctv") + 1], "q8_0")
        self.assertEqual(args[args.index("-fa") + 1], "on")

    def test_cache_normal_aucun_argument(self):
        args = server.build_args(self.model, dict(self.base, cache_type="f16", flash_attn="auto"), 8701, 8700)
        self.assertNotIn("-ctk", args)
        self.assertNotIn("-fa", args)


if __name__ == "__main__":
    unittest.main()
