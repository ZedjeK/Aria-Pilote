import contextlib
import io
import unittest

import cli
from stock.alertes import alertes, charger_seuils
from stock.depot import charger
from stock.rapport import rapport_texte


class Alertes(unittest.TestCase):
    def setUp(self):
        self.produits = charger("donnees/stock.csv")
        self.seuils = charger_seuils("donnees/seuils.csv")

    def test_seuils(self):
        self.assertEqual(self.seuils, {"VIS4": 100, "CHEV": 20, "COLLE": 5, "GANTS": 4})

    def test_alertes_triees_par_manque(self):
        # (ref, quantité, seuil) des produits sous leur seuil, du plus gros manque au plus petit ;
        # un produit sans seuil (SCIE) n'est jamais en alerte
        self.assertEqual(alertes(self.produits, self.seuils), [("CHEV", 8, 20), ("GANTS", 0, 4), ("COLLE", 2, 5)])

    def test_rapport_avec_seuils(self):
        texte = rapport_texte(self.produits, self.seuils)
        self.assertTrue(texte.endswith("Alertes (3) :\n- CHEV : 8 / 20\n- GANTS : 0 / 4\n- COLLE : 2 / 5"), texte)
        self.assertIn("Valeur du stock : 64.50 €", texte)

    def test_ligne_de_commande(self):
        sortie = io.StringIO()
        with contextlib.redirect_stdout(sortie):
            code = cli.main(["--alertes"])
        self.assertEqual(code, 1)  # 1 dès qu'au moins un produit est en alerte : utilisable dans un script
        self.assertEqual(sortie.getvalue().strip(), "Alertes (3) :\n- CHEV : 8 / 20\n- GANTS : 0 / 4\n- COLLE : 2 / 5")


if __name__ == "__main__":
    unittest.main()
