from outils.montants import arrondi, calcul_ttc


def total_facture(lignes, taux):
    """Total TTC d'une facture : chaque ligne a un prix unitaire HT et une quantité."""
    return arrondi(sum(calcul_ttc(ligne["prix"] * ligne["qte"], taux) for ligne in lignes))
