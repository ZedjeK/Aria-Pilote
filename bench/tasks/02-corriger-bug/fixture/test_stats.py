import unittest

from stats import moyenne, moyenne_mobile


class TestStats(unittest.TestCase):
    def test_moyenne(self):
        self.assertEqual(moyenne([2, 4, 6]), 4)

    def test_moyenne_mobile(self):
        self.assertEqual(moyenne_mobile([1, 2, 3, 4], 2), [1.5, 2.5, 3.5])

    def test_fenetre_complete(self):
        self.assertEqual(moyenne_mobile([5, 7], 2), [6])


if __name__ == "__main__":
    unittest.main()
