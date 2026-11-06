"""Journal des ventes : ne fait pas de calcul de TVA lui-même."""

from compta.export import ligne_export


def journal(ventes, taux):
    return "\n".join(ligne_export(v, taux) for v in ventes)
