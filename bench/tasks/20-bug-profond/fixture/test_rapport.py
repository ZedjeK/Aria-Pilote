import unittest

from ventes.chargement import lire_ventes
from ventes.rapport import rapport_mensuel, rapport_trimestre


class Rapport(unittest.TestCase):
    def setUp(self):
        self.ventes = lire_ventes("donnees/ventes.csv")

    def test_janvier(self):
        self.assertEqual(rapport_mensuel(self.ventes, "2026-01"),
                         "Ventes 2026-01 : 1 950,50 €\n  Durand : 1 500,00 €\n  Martin : 450,50 €\nMeilleur client : Durand")

    def test_fevrier(self):
        self.assertEqual(rapport_mensuel(self.ventes, "2026-02"),
                         "Ventes 2026-02 : 1 970,00 €\n  Petit : 1 050,00 €\n  Martin : 920,00 €\nMeilleur client : Petit")

    def test_trimestre(self):
        texte = rapport_trimestre(self.ventes, ["2026-01", "2026-02", "2026-03"])
        self.assertIn("Ventes 2026-03 : 99,90 €\n  Durand : 99,90 €\nMeilleur client : Durand", texte)


if __name__ == "__main__":
    unittest.main()
