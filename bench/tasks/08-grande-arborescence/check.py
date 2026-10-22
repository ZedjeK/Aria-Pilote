from pathlib import Path

from checks import missing, unchanged

APPELANTS = ["m07", "m23", "m38", "m51"]


def setup(ws):
    # 60 modules ; 4 appellent appliquer_remise (définie dans remises.py), 2 ont des noms proches en leurre
    folder = Path(ws) / "modules"
    folder.mkdir()
    (folder / "remises.py").write_text(
        "def appliquer_remise(prix, taux=0.1):\n    return round(prix * (1 - taux), 2)\n", encoding="utf-8")
    for i in range(1, 61):
        name = f"m{i:02d}"
        lines = [f'"""Module {name} : traitement des commandes, lot {i}."""', ""]
        if name in APPELANTS:
            lines += ["from .remises import appliquer_remise", "", "",
                      f"def prix_final_{i}(prix):", "    return appliquer_remise(prix, 0.15)"]
        elif i in (12, 44):
            lines += ["", f"def appliquer_remise_fidelite_{i}(prix):", "    return prix * 0.95"]
        else:
            lines += ["", f"def total_{i}(lignes):", "    return sum(lignes)"]
        (folder / f"{name}.py").write_text("\n".join(lines) + "\n", encoding="utf-8")


def check(ws, run):
    if not unchanged(ws, run):
        return False, "fichiers modifiés alors que la question ne demandait que de lire"
    absent = missing(run, *APPELANTS)
    if absent:
        return False, f"modules oubliés : {', '.join(absent)}"
    # m12 et m44 (noms proches) sont souvent cités pour être exclus : on ne pénalise pas leur mention
    return True, "ok (délégué)" if any(c["name"] == "delegate" for c in run["calls"]) else "ok"
