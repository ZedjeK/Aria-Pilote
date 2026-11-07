"""Prise en main : modèles conseillés selon la machine, modèle téléchargé mis par défaut et réglé par l'optimiseur."""

import copy
import json
import shutil
import unittest
from unittest import mock

from base import GIB, HOME, client, fake_gguf, model_data

from aria_pilote import config, downloads, hub, main, models

MODELS_DIR = HOME / "models"


def keep_config(test):
    saved = copy.deepcopy(config.get())

    def restore():
        with config._lock:
            config._data = saved
            config._save_locked()
    test.addCleanup(restore)


def memory(vram_gib):
    return {"vram": int(vram_gib * GIB) if vram_gib else None, "ram": 32 * GIB}


class Recommended(unittest.TestCase):
    def setUp(self):
        self.addCleanup(models.scan, True)
        self.addCleanup(shutil.rmtree, MODELS_DIR, ignore_errors=True)

    def recommended(self, vram_gib):
        with mock.patch.object(hub, "machine_memory", return_value=memory(vram_gib)):
            return hub.recommended()

    def test_liste_bien_formee(self):
        picks = json.loads(hub.RECOMMENDED_PATH.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(picks), 3)
        for pick in picks:
            with self.subTest(modele=pick["name"]):
                self.assertRegex(pick["repo"], hub.REPO_RE)
                self.assertTrue(pick["file"].endswith(".gguf"))
                self.assertGreater(pick["size"], 0)
                self.assertTrue(pick["note"])
        # Un modèle par palier, du plus léger au plus lourd
        self.assertEqual([p["vram_gb"] for p in picks], sorted({p["vram_gb"] for p in picks}))
        self.assertEqual([p["size"] for p in picks], sorted(p["size"] for p in picks))

    def test_le_plus_grand_qui_tient_sur_le_gpu(self):
        for vram, palier in ((8, 8), (12, 12), (16, 16), (24, 24)):
            with self.subTest(vram=vram):
                data = self.recommended(vram)
                best = data["models"][data["best"]]
                self.assertEqual(best["vram_gb"], palier)
                self.assertEqual(best["fit"], "gpu")

    def test_petite_carte_ou_sans_nvidia_le_plus_leger(self):
        for vram in (None, 2):
            with self.subTest(vram=vram):
                data = self.recommended(vram)
                self.assertEqual(data["best"], 0)
                self.assertEqual(data["models"][0]["vram_gb"], 0)
        self.assertEqual({m["fit"] for m in self.recommended(None)["models"]}, {"unknown"})

    def test_modele_deja_telecharge(self):
        pick = json.loads(hub.RECOMMENDED_PATH.read_text(encoding="utf-8"))[1]
        fake_gguf(MODELS_DIR, f"{pick['repo']}/{pick['file']}")
        models.scan(force=True)
        data = self.recommended(16)
        entry = data["models"][1]
        self.assertTrue(entry["downloaded"])
        self.assertEqual(models.get(entry["model_id"]).file, pick["file"])
        self.assertFalse(data["models"][0]["downloaded"])

    def test_deja_en_file_par_l_api(self):
        pick = json.loads(hub.RECOMMENDED_PATH.read_text(encoding="utf-8"))[0]
        job = downloads.Job(pick["repo"], [{"path": pick["file"], "size": pick["size"], "sha256": None}], "essai")
        downloads.manager.jobs[job.id] = job
        self.addCleanup(downloads.manager.jobs.pop, job.id, None)
        with mock.patch.object(hub, "machine_memory", return_value=memory(16)):
            data = client().get("/api/hub/recommended").json()
        self.assertEqual([m["queued"] for m in data["models"]][:2], [True, False])
        job.status = "done"
        with mock.patch.object(hub, "machine_memory", return_value=memory(16)):
            self.assertFalse(client().get("/api/hub/recommended").json()["models"][0]["queued"])


class Downloaded(unittest.TestCase):
    """Fin d'un téléchargement : le modèle est retrouvé, puis adopté (par défaut, réglé)."""

    def setUp(self):
        keep_config(self)
        self.addCleanup(models.scan, True)
        self.addCleanup(shutil.rmtree, MODELS_DIR, ignore_errors=True)
        self.addCleanup(setattr, downloads.manager, "on_model", downloads.manager.on_model)

    def finished_job(self, with_vision=True):
        fake_gguf(MODELS_DIR, "editeur/depot-GGUF/modele-Q4_K_M.gguf")
        files = [{"path": "modele-Q4_K_M.gguf", "size": 16, "sha256": None}]
        if with_vision:  # le projecteur vision passe avant le modèle : ce n'est pas lui qu'on charge
            fake_gguf(MODELS_DIR, "editeur/depot-GGUF/mmproj-F16.gguf")
            files.insert(0, {"path": "mmproj-F16.gguf", "size": 16, "sha256": None})
        models.scan(force=True)
        return downloads.Job("editeur/depot-GGUF", files, "essai")

    def test_identifiant_et_notes(self):
        job = self.finished_job()
        downloads.manager.on_model = lambda model: [f"vu : {model.file}"]
        downloads.manager._adopt(job)
        self.assertEqual(models.get(job.model_id).file, "modele-Q4_K_M.gguf")
        self.assertEqual(job.to_dict()["notes"], ["vu : modele-Q4_K_M.gguf"])

    def test_un_rate_de_l_adoption_ne_fait_pas_echouer(self):
        job = self.finished_job(with_vision=False)

        def boom(model):
            raise RuntimeError("en-tête illisible")
        downloads.manager.on_model = boom
        downloads.manager._adopt(job)
        self.assertIsNotNone(job.model_id)
        self.assertIn("en-tête illisible", job.notes[0])

    def test_par_defaut_seulement_s_il_n_y_en_a_pas(self):
        job = self.finished_job(with_vision=False)
        downloads.manager.on_model = None
        downloads.manager._adopt(job)
        model = models.get(job.model_id)
        config.update({"default_model": ""})
        self.assertIn("modèle par défaut", main._adopt_downloaded(model))
        self.assertEqual(config.get()["default_model"], model.id)
        config.update({"default_model": "autre"})
        self.assertNotIn("modèle par défaut", main._adopt_downloaded(model))
        self.assertEqual(config.get()["default_model"], "autre")

    def test_reglages_du_compromis_sans_ecraser_les_siens(self):
        qwen, meta = model_data("qwen")
        config.update({"default_model": "autre"})
        hw = {"cpu": {"cores": 12}}
        with mock.patch.object(main, "_budget", return_value=(hw, int(13.5 * GIB), None)), \
                mock.patch.object(main, "_meta_of", return_value=meta):
            config.forget_model(qwen.key)
            notes = main._adopt_downloaded(qwen)
            saved = config.model_settings(qwen.key)
            self.assertEqual((saved["ctx_size"], saved["cache_type"], saved["gpu_layers"]), (32768, "q8_0", 99))
            self.assertIn("contexte 32k", notes[0])
            # Des réglages à lui (choisis à la main, ou par une adoption précédente) : on n'y touche plus
            config.save_model_settings(qwen.key, {**saved, "ctx_size": 8192})
            self.assertEqual(main._adopt_downloaded(qwen), [])
            self.assertEqual(config.model_settings(qwen.key)["ctx_size"], 8192)
        config.forget_model(qwen.key)


if __name__ == "__main__":
    unittest.main()
