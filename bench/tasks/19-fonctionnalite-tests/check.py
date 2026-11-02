from checks import python, unchanged

HIDDEN = """
from stock.alertes import alertes
from stock.modele import Produit
produits = [Produit("A", "a", 1, 2.0), Produit("B", "b", 9, 1.0), Produit("C", "c", 0, 3.0), Produit("D", "d", 4, 1.0)]
resultat = alertes(produits, {"A": 5, "B": 9, "C": 2, "E": 3})
assert resultat == [("A", 1, 5), ("C", 0, 2)], resultat
assert alertes(produits, {}) == []
"""


def check(ws, run):
    if not unchanged(ws, run, "test_alertes.py", "test_stock.py"):
        return False, "les tests ont été modifiés"
    code, out = python(ws, "-m", "unittest")
    if code != 0:
        return False, "les tests échouent : " + out.strip().splitlines()[-1]
    code, out = python(ws, "-c", HIDDEN)
    if code != 0:
        return False, "alertes() fausse sur d'autres données : " + out.strip().splitlines()[-1]
    return True, "ok"
