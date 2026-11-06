"""Calculs de montants communs à tout le projet."""


def calcul_ttc(ht, taux):
    """Montant TTC arrondi au centime, à partir d'un montant hors taxes et d'un taux de TVA (0.20 pour 20 %)."""
    return round(ht * (1 + taux), 2)


def arrondi(montant):
    return round(montant, 2)
