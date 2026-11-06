from checks import python, unchanged

# La cause est dans agregats.total_par_client : un contournement dans rapport.py ou synthese.py ne suffit pas
HIDDEN = """
from ventes.agregats import total_par_client
premier = total_par_client([{"client": "A", "montant": 10.0}])
second = total_par_client([{"client": "A", "montant": 5.0}])
assert second == {"A": 5.0}, second
assert premier == {"A": 10.0}, premier
"""


def check(ws, run):
    if not unchanged(ws, run, "test_rapport.py"):
        return False, "test_rapport.py a été modifié"
    code, out = python(ws, "-m", "unittest")
    if code != 0:
        return False, "les tests échouent : " + out.strip().splitlines()[-1]
    code, out = python(ws, "-c", HIDDEN)
    if code != 0:
        return False, "cause non corrigée à la source (total_par_client) : " + out.strip().splitlines()[-1]
    return True, "ok"
