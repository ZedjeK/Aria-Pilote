"""Modèles : identifiant stable d'une version à l'autre, migration des réglages, nom affiché."""

import copy
import tempfile
import unittest
from pathlib import Path

from base import HOME, fake_gguf

from aria_pilote import config, models


class StableKeys(unittest.TestCase):
    def setUp(self):
        saved = copy.deepcopy(config.get())

        def restore():
            with config._lock:
                config._data = saved
                config._save_locked()
            models.scan(force=True)
        self.addCleanup(restore)
        base = Path(tempfile.mkdtemp(dir=HOME))
        # Le même modèle dans le dossier de deux versions successives de l'appli
        self.old_dir = base / "Aria-Pilote-0.8.0" / "models"
        self.new_dir = base / "Aria-Pilote-0.9.0" / "models"
        fake_gguf(self.old_dir)
        fake_gguf(self.new_dir)

    def scan(self, *dirs):
        config.update({"model_dirs": [str(d) for d in dirs]})
        return models.scan(force=True)

    def test_meme_identifiant_apres_changement_de_dossier(self):
        old, new = self.scan(self.old_dir)[0], self.scan(self.new_dir)[0]
        self.assertEqual((old.id, old.key), (new.id, new.key))
        self.assertEqual(new.key, "editeur/depot-GGUF/modele-Q4_K_M.gguf")

    def test_migration_des_anciens_reglages(self):
        old_path = str(self.old_dir / "editeur" / "depot-GGUF" / "modele-Q4_K_M.gguf")
        missing = r"C:\ailleurs\modele-supprime.gguf"
        config.update({"model_overrides": {old_path: {"ctx_size": 32768, "gpu_layers": 40}, missing: {"ctx_size": 4096}}})
        new = self.scan(self.new_dir)[0]
        overrides = config.get()["model_overrides"]
        self.assertEqual(overrides.get(new.key), {"ctx_size": 32768, "gpu_layers": 40})
        self.assertNotIn(old_path, overrides)
        self.assertIn(missing, overrides)  # modèle introuvable : laissé tel quel
        self.assertEqual(config.model_settings(new.key)["ctx_size"], 32768)
        self.scan(self.new_dir)
        self.assertEqual(list(config.get()["model_overrides"]).count(new.key), 1)

    def test_meme_chemin_dans_deux_dossiers(self):
        both = self.scan(self.old_dir, self.new_dir)
        self.assertEqual(len({m.id for m in both}), 2)
        self.assertTrue(any(m.key.startswith("1:") for m in both))


class DisplayName(unittest.TestCase):
    def test_nom_trop_pauvre_remplace_par_le_fichier(self):
        self.assertEqual(models._display_name("Hf", Path("Ternary-Bonsai-2-27B-PQ2_0.gguf")), "Ternary-Bonsai-2-27B-PQ2_0")
        self.assertEqual(models._display_name("Qwen38 Ara v5", Path("x.gguf")), "Qwen38 Ara v5")
        self.assertEqual(models._display_name(None, Path("modele.gguf")), "modele")


if __name__ == "__main__":
    unittest.main()
