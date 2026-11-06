from pathlib import Path

from checks import python, unchanged

HIDDEN = """
import inspect
from outils.montants import prix_ttc
params = list(inspect.signature(prix_ttc).parameters.values())
assert [p.name for p in params] == ["montant_ht", "taux_tva"], params
assert params[1].default == 0.20, params[1].default
assert prix_ttc(100) == 120.0 and prix_ttc(montant_ht=10, taux_tva=0.055) == 10.55
"""


def check(ws, run):
    if not unchanged(ws, run, "test_projet.py"):
        return False, "test_projet.py a été modifié"
    left = [p.relative_to(ws).as_posix() for p in Path(ws).rglob("*.py")
            if "__pycache__" not in p.parts and "calcul_ttc" in p.read_text(encoding="utf-8", errors="replace")]
    if left:
        return False, f"calcul_ttc encore présent : {', '.join(left)}"
    code, out = python(ws, "-c", HIDDEN)
    if code != 0:
        return False, "signature de prix_ttc incorrecte : " + out.strip().splitlines()[-1]
    code, out = python(ws, "-m", "unittest")
    if code != 0:
        return False, "les tests échouent : " + out.strip().splitlines()[-1]
    return True, "ok"
