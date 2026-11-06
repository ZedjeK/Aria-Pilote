from checks import python, unchanged

# Les dates sont bien des datetime.date, du chargement jusqu'à emprunter et rendre
HIDDEN = """
from datetime import date
from mediatheque import depot, emprunts as ops
liste = depot.charger_emprunts()
assert all(isinstance(e.date_emprunt, date) for e in liste), "date_emprunt n'est pas une date"
assert all(e.date_retour is None or isinstance(e.date_retour, date) for e in liste), "date_retour n'est pas une date"
e = ops.emprunter(liste, "L002", "A02", date(2026, 11, 15))
assert e.date_emprunt == date(2026, 11, 15), e
ops.rendre(liste, "L002", date(2026, 11, 20))
assert e.date_retour == date(2026, 11, 20), e
"""


def check(ws, run):
    for name in ("test_mediatheque.py", "donnees/emprunts.csv", "donnees/documents.csv", "donnees/adherents.csv"):
        if not unchanged(ws, run, name):
            return False, f"{name} a été modifié"
    code, out = python(ws, "-c", HIDDEN)
    if code != 0:
        return False, "dates pas converties : " + out.strip().splitlines()[-1]
    code, out = python(ws, "-m", "unittest")
    if code != 0:
        return False, "les tests échouent : " + out.strip().splitlines()[-1]
    return True, "ok"
