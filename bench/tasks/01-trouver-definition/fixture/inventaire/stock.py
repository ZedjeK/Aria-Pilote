from .reglages import SEUIL_ALERTE


def en_alerte(article):
    return article["quantite"] < SEUIL_ALERTE


def a_commander(articles):
    return [a["nom"] for a in articles if en_alerte(a)]
