"""Fiche d'un modèle (lot 3) : nouveaux réglages de chargement, enregistrement sans charger, réglages par défaut."""
import json
import unittest
from types import SimpleNamespace

from base import HOME, ORIGIN, client, fake_gguf, model_data  # en premier : Aria tourne dans un dossier temporaire

from aria_pilote import config, models, server


class ArgsTest(unittest.TestCase):
    def setUp(self):
        self.base = dict(config.DEFAULTS["load_defaults"])

    def args(self, experts=None, mtp_layers=None, **settings):
        model = SimpleNamespace(path="m.gguf", name="m", mmproj=None, experts=experts, mtp_layers=mtp_layers)
        return server.build_args(model, dict(self.base, **settings), 8701, 8700)

    def value(self, args, flag):
        return args[args.index(flag) + 1]

    def test_par_defaut_rien_de_plus(self):
        args = self.args()
        for flag in ("-b", "-ub", "-ctk", "-ctv", "-ncmoe", "-lm", "--spec-type"):
            self.assertNotIn(flag, args)

    def test_cache_k_et_v_separes(self):
        args = self.args(cache_type="f16", cache_type_v="q8_0")
        self.assertNotIn("-ctk", args)
        self.assertEqual(self.value(args, "-ctv"), "q8_0")
        self.assertEqual(self.value(args, "-fa"), "on")  # V compressé : flash attention obligatoire
        args = self.args(cache_type="q8_0", cache_type_v="")
        self.assertEqual((self.value(args, "-ctk"), self.value(args, "-ctv")), ("q8_0", "q8_0"))  # V suit K

    def test_batch_mode_de_chargement(self):
        args = self.args(batch_size=4096, ubatch_size=1024, load_mode="mlock")
        self.assertEqual((self.value(args, "-b"), self.value(args, "-ub"), self.value(args, "-lm")), ("4096", "1024", "mlock"))

    def test_experts_et_mtp_seulement_si_le_modele_les_a(self):
        self.assertNotIn("-ncmoe", self.args(n_cpu_moe=10))
        self.assertEqual(self.value(self.args(experts=128, n_cpu_moe=10), "-ncmoe"), "10")
        self.assertNotIn("--spec-type", self.args(mtp=True))
        args = self.args(mtp_layers=1, mtp=True, mtp_draft=3)
        self.assertEqual((self.value(args, "--spec-type"), self.value(args, "--spec-draft-n-max")), ("draft-mtp", "3"))


class CheckTest(unittest.TestCase):
    def test_valeurs_refusees(self):
        for bad in ({"cache_type_v": "q2"}, {"load_mode": "turbo"}, {"ctx_size": 100}, {"batch_size": "beaucoup"},
                    {"batch_size": 512, "ubatch_size": 1024}, {"flash_attn": "peut-être"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                config.check_load_settings(bad)

    def test_nettoyage(self):
        clean = config.check_load_settings({"ctx_size": "8192", "mtp": 1, "inconnu": "x", "cache_type_v": ""})
        self.assertEqual(clean, {"ctx_size": 8192, "mtp": True, "cache_type_v": ""})


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.http = client()
        self.dir = HOME / "modeles-fiche"
        fake_gguf(self.dir)
        saved = config.get()["model_dirs"]
        config.update({"model_dirs": [str(self.dir)]})
        self.addCleanup(lambda: (config.update({"model_dirs": saved}), models.scan(force=True)))
        self.model = models.scan(force=True)[0]

    def url(self, suffix=""):
        return f"/api/models/{self.model.id}{suffix}"

    def test_enregistrer_sans_charger_puis_revenir_au_defaut(self):
        r = self.http.put(self.url("/settings"), json={"settings": {"ctx_size": 16384, "cache_type_v": "q8_0"}}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["custom"])
        self.assertEqual(r.json()["settings"]["cache_type_v"], "q8_0")
        # Un second enregistrement remplace le premier : un champ qui n'y est plus revient au défaut
        r = self.http.put(self.url("/settings"), json={"settings": {"ctx_size": 8192}}, headers=ORIGIN)
        self.assertEqual(r.json()["settings"]["cache_type_v"], "")
        r = self.http.delete(self.url("/settings"), headers=ORIGIN)
        self.assertFalse(r.json()["custom"])
        self.assertFalse(config.has_model_settings(self.model.key))

    def test_refus(self):
        r = self.http.put(self.url("/settings"), json={"settings": {"load_mode": "turbo"}}, headers=ORIGIN)
        self.assertEqual(r.status_code, 400)
        r = self.http.put(self.url("/settings"), json={"settings": {"engine": "inconnu"}}, headers=ORIGIN)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.http.put("/api/models/fantome/settings", json={"settings": {}}, headers=ORIGIN).status_code, 404)

    def test_reglages_par_defaut_valides(self):
        r = self.http.put("/api/config", json={"load_defaults": {"cache_type_v": "q2"}}, headers=ORIGIN)
        self.assertEqual(r.status_code, 400)
        saved = config.get()["load_defaults"]
        self.addCleanup(config.update, {"load_defaults": saved})
        r = self.http.put("/api/config", json={"load_defaults": {"batch_size": 4096}}, headers=ORIGIN)
        self.assertEqual(r.json()["load_defaults"]["batch_size"], 4096)

    def test_details_du_fichier(self):
        r = self.http.get(self.url("/details"), headers=ORIGIN)
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.json()["rows"], list)


class DescribeTest(unittest.TestCase):
    def test_en_tete_de_qwen_en_clair(self):
        info, meta = model_data("qwen")
        model = SimpleNamespace(arch=meta.get("general.architecture"), size_label="27B", quant="Q4_K_M", mmproj="x/mmproj.gguf")
        rows = {r["label"]: r["value"] for r in models.describe(model, meta)}
        self.assertEqual(rows["Architecture"], meta["general.architecture"])
        self.assertIn("Contexte maximal", rows)
        self.assertIn("Couches", rows)
        self.assertEqual(rows["Projecteur vision"], "mmproj.gguf")


if __name__ == "__main__":
    unittest.main()
