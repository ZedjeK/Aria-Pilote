from outils.montants import calcul_ttc


def total_panier(articles, taux=0.20):
    """Total TTC du panier : une liste de prix HT."""
    return round(sum(calcul_ttc(article, taux=taux) for article in articles), 2)
