import unittest

from convertir import arrondi, celsius_vers_fahrenheit, fahrenheit_vers_celsius, kelvin


class TestConvertir(unittest.TestCase):
    def test_ebullition(self):
        self.assertEqual(celsius_vers_fahrenheit(100), 212)

    def test_glace(self):
        self.assertEqual(fahrenheit_vers_celsius(32), 0)

    def test_arrondi_superieur(self):
        self.assertEqual(arrondi(2.7), 3)

    def test_kelvin_negatif(self):
        self.assertAlmostEqual(kelvin(-10), 263.15)

    def test_kelvin_positif(self):
        self.assertAlmostEqual(kelvin(20), 293.15)


if __name__ == "__main__":
    unittest.main()
