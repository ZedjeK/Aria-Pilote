import unittest

from stock.depot import charger
from stock.mouvements import StockInsuffisant, entree, sortie
from stock.rapport import rapport_texte


class Stock(unittest.TestCase):
    def setUp(self):
        self.produits = charger("donnees/stock.csv")

    def test_chargement(self):
        self.assertEqual([p.ref for p in self.produits], ["VIS4", "CHEV", "COLLE", "SCIE", "GANTS"])
        self.assertEqual(self.produits[2].quantite, 2)

    def test_mouvements(self):
        vis = self.produits[0]
        entree(vis, 10)
        sortie(vis, 30)
        self.assertEqual(vis.quantite, 100)
        with self.assertRaises(StockInsuffisant):
            sortie(vis, 101)

    def test_rapport_sans_seuils(self):
        texte = rapport_texte(self.produits)
        self.assertTrue(texte.endswith("Valeur du stock : 64.50 €"))
        self.assertNotIn("Alertes", texte)


if __name__ == "__main__":
    unittest.main()
