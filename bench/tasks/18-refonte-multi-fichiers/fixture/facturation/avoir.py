from outils import montants


def montant_avoir(ht, taux):
    """Un avoir est une facture négative."""
    return -montants.calcul_ttc(ht, taux)
