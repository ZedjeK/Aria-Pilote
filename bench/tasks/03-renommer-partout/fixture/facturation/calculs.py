TAUX_TVA = 0.20


def calc_tva(montant_ht):
    """Montant de TVA pour un montant hors taxes."""
    return round(montant_ht * TAUX_TVA, 2)


def total_ttc(montant_ht):
    return round(montant_ht + calc_tva(montant_ht), 2)
