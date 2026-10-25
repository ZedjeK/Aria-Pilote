from checks import missing


def check(ws, run):
    # Charger une compétence (prof, securite…) est le comportement voulu depuis le chantier 3 ; tout autre outil est de trop
    useless = [c["name"] for c in run["calls"] if c["name"] != "load_skill"]
    if useless:
        return False, "outils inutiles : " + ", ".join(useless)
    if len(run["final"].strip()) < 80:
        return False, "réponse vide ou trop courte"
    absent = missing(run, "TCP", "UDP")
    if absent:
        return False, "manque : " + ", ".join(absent)
    return True, "ok"
