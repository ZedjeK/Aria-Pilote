from checks import missing


def check(ws, run):
    if run["calls"]:
        return False, "outils inutiles : " + ", ".join(c["name"] for c in run["calls"])
    if len(run["final"].strip()) < 20:
        return False, "réponse vide ou trop courte"
    absent = missing(run)
    if absent:
        return False, "manque : " + ", ".join(absent)
    return True, "ok"
