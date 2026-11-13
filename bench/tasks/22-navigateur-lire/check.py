"""L'agent a ouvert la page dans le navigateur et trouvé la baie du switch de cœur, enfouie loin du début."""

from checks import missing, used


def check(ws, run):
    if not used(run, "naviguer"):
        return False, "la page n'a pas été ouverte avec naviguer"
    if not (used(run, "chercher_page") or used(run, "lire_page")):
        return False, "l'information est hors de la première partie : ni chercher_page ni lire_page"
    if missing(run, "42"):
        return False, "la baie 42 n'est pas dans la réponse"
    return True, "baie 42 trouvée"
