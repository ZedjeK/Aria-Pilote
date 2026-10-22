from pathlib import Path

from checks import python

ATTENDU = "Câble RJ45 : 12.50 HT, TVA 2.50, 15.00 TTC\n{'ht': 150, 'tva': 30.0}"


def check(ws, run):
    restes = [p.name for p in Path(ws).rglob("*.py") if "calc_tva" in p.read_text(encoding="utf-8")]
    if restes:
        return False, f"calc_tva encore présent dans : {', '.join(restes)}"
    if "def calculer_tva(" not in (Path(ws) / "facturation" / "calculs.py").read_text(encoding="utf-8"):
        return False, "def calculer_tva absente de calculs.py"
    code, out = python(ws, "main.py")
    if code != 0 or out.strip() != ATTENDU:
        return False, "main.py ne donne plus le résultat attendu : " + out.strip()[-120:]
    return True, "ok"
