import unittest

from boutique.livraison import frais_livraison
from boutique.panier import total_panier
from compta.journal import journal
from compta.tva import tva_collectee
from facturation.avoir import montant_avoir
from facturation.devis import total_devis
from facturation.facture import total_facture


class Projet(unittest.TestCase):
    def test_facture(self):
        self.assertEqual(total_facture([{"prix": 10, "qte": 3}, {"prix": 5, "qte": 2}], 0.20), 48.0)

    def test_avoir(self):
        self.assertEqual(montant_avoir(50, 0.10), -55.0)

    def test_devis(self):
        self.assertEqual(total_devis([100, 250]), 420.0)

    def test_panier(self):
        self.assertEqual(total_panier([10, 20]), 36.0)
        self.assertEqual(total_panier([10], taux=0.055), 10.55)

    def test_livraison(self):
        self.assertEqual(frais_livraison(60), 0.0)
        self.assertEqual(frais_livraison(20), 5.88)

    def test_compta(self):
        self.assertEqual(tva_collectee(100, 0.20), 20.0)
        self.assertEqual(journal([100, 12.5], 0.20), "100.00;120.00\n12.50;15.00")


if __name__ == "__main__":
    unittest.main()
