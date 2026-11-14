import unittest

from panier import total_ttc


class TestPanier(unittest.TestCase):
    def test_sans_remise(self):
        self.assertEqual(total_ttc([(10, 2), (5, 1)]), 30.0)

    def test_avec_remise(self):
        self.assertEqual(total_ttc([(10, 2), (5, 1)], remise=0.1), 27.0)

    def test_panier_vide(self):
        self.assertEqual(total_ttc([]), 0.0)


if __name__ == "__main__":
    unittest.main()
