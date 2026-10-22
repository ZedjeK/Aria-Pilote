from checks import missing, unchanged, used


def check(ws, run):
    if not unchanged(ws, run, "convertir.py", "test_convertir.py"):
        return False, "fichiers modifiés alors qu'il ne fallait rien corriger"
    if not used(run, "run_command"):
        return False, "les tests n'ont pas été lancés"
    absent = missing(run, "test_arrondi_superieur", "test_kelvin_negatif")
    if absent:
        return False, f"tests en échec non cités : {', '.join(absent)}"
    return True, "ok"
