from checks import used, unchanged


def check(ws, run):
    if not unchanged(ws, run):
        return False, "fichiers modifiés avant l'accord sur le plan"
    if used(run, "propose_plan"):
        return True, "ok"
    if len(run["final"].strip()) > 80:
        return True, "ok (plan écrit en texte, sans propose_plan)"
    return False, "aucun plan proposé"
