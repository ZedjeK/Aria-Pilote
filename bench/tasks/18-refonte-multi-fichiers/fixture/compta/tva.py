from outils.montants import calcul_ttc


def tva_collectee(ht, taux):
    return round(calcul_ttc(ht, taux) - ht, 2)
