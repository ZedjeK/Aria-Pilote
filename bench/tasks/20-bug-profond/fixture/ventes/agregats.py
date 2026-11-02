def total_general(ventes):
    return round(sum(v["montant"] for v in ventes), 2)


def total_par_client(ventes, cumul={}):
    """Total des ventes par client ; « cumul » permet d'ajouter à des totaux existants."""
    for v in ventes:
        cumul[v["client"]] = round(cumul.get(v["client"], 0) + v["montant"], 2)
    return cumul


def meilleur_client(totaux):
    return max(totaux, key=totaux.get) if totaux else None
