"""Outils de mesure (banc, essais) : un motif de modèle désigne un seul modèle, jamais le premier venu."""
import sys
import unittest
from pathlib import Path

import base  # noqa: F401  (en premier : Aria tourne dans un dossier temporaire)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import bench  # noqa: E402

MODELS = [  # les noms et chemins réels qui ont causé la confusion
    {"name": "Qwen38 Ara v5", "path": "models/0bserverx/Qwen3.8-27B-Heretic-Abliterated-Uncensored-GGUF/RVN-IQ3_XXS.gguf"},
    {"name": "Ternary-Bonsai-2-27B-Abliterated-v2-PQ2_0-MTP",
     "path": "models/BoldingBuilds/Ternary-Bonsai-2-27B-Abliterated-v2-PQ2_0-MTP-GGUF/Ternary-Bonsai-2-27B-Abliterated-v2-PQ2_0-MTP.gguf"},
    {"name": "Ternary-Bonsai-2-27B-PQ2_0", "path": "models/prism-ml/Ternary-Bonsai-2-27B-gguf/Ternary-Bonsai-2-27B-PQ2_0.gguf"},
    {"name": "Ternary-Bonsai-2-27B-mmproj-Q8_0", "path": "models/prism-ml/Ternary-Bonsai-2-27B-gguf/Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf"},
]


class FakeClient:
    def get(self, url):
        return type("R", (), {"json": lambda self: MODELS})()


class FindModelTest(unittest.TestCase):
    def find(self, pattern):
        return bench.find_model(FakeClient(), pattern)["name"]

    def test_le_nom_passe_avant_le_chemin(self):
        # « abliterated » est dans le chemin de Qwen, mais seul le Bonsai le porte dans son nom
        self.assertEqual(self.find("abliterated"), "Ternary-Bonsai-2-27B-Abliterated-v2-PQ2_0-MTP")
        self.assertEqual(self.find("qwen"), "Qwen38 Ara v5")
        self.assertEqual(self.find("bonsai-2-27b-pq2"), "Ternary-Bonsai-2-27B-PQ2_0")

    def test_le_chemin_si_le_nom_ne_suffit_pas(self):
        self.assertEqual(self.find("heretic"), "Qwen38 Ara v5")

    def test_motif_ambigu_refuse_avec_les_candidats(self):
        with self.assertRaises(SystemExit) as ctx:
            self.find("bonsai")
        message = str(ctx.exception)
        self.assertIn("plusieurs modèles", message)
        self.assertIn("Ternary-Bonsai-2-27B-PQ2_0", message)
        self.assertNotIn("mmproj", message)  # le projecteur vision n'est jamais un candidat

    def test_motif_introuvable(self):
        with self.assertRaises(SystemExit):
            self.find("llama-70b")


if __name__ == "__main__":
    unittest.main()
