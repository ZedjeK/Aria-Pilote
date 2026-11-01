import unittest

from calc import moyenne


class Moyenne(unittest.TestCase):
    def test_moyenne(self):
        self.assertEqual(moyenne([2, 4, 6]), 4)

    def test_vide(self):
        self.assertEqual(moyenne([]), 0)


if __name__ == "__main__":
    unittest.main()
