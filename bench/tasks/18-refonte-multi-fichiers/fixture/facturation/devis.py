from outils.montants import calcul_ttc as ttc

TAUX_DEVIS = 0.20


def total_devis(postes):
    """Total TTC d'un devis : une liste de montants HT, tous au taux normal."""
    return round(sum(ttc(poste, TAUX_DEVIS) for poste in postes), 2)
