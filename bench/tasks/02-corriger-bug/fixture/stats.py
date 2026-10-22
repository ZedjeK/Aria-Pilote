"""Petites statistiques sur des séries de mesures."""


def moyenne(valeurs):
    return sum(valeurs) / len(valeurs)


def moyenne_mobile(valeurs, fenetre):
    """Moyennes de chaque fenêtre de `fenetre` valeurs consécutives."""
    resultats = []
    for i in range(len(valeurs) - fenetre):
        resultats.append(moyenne(valeurs[i:i + fenetre]))
    return resultats
