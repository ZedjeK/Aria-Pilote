"""Calcul du total d'un panier."""

TVA = 0.2


def total_ttc(lignes, remise=0.0):
    """Total TTC d'un panier. `lignes` : liste de (prix_ht, quantite). `remise` : fraction du total HT (0.1 = 10 %)."""
    total_ht = sum(prix * quantite for prix, quantite in lignes)
    total_ht -= remise
    return round(total_ht * (1 + TVA), 2)
