import outils.montants as m


def ligne_export(ht, taux):
    """Une ligne du fichier d'export comptable : HT;TTC."""
    return f"{ht:.2f};{m.calcul_ttc(ht, taux):.2f}"
