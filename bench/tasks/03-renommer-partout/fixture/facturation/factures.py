from .calculs import calc_tva, total_ttc


def ligne_facture(libelle, montant_ht):
    return f"{libelle} : {montant_ht:.2f} HT, TVA {calc_tva(montant_ht):.2f}, {total_ttc(montant_ht):.2f} TTC"
