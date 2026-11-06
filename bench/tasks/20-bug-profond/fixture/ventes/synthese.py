from ventes.agregats import meilleur_client, total_general, total_par_client


def synthese(ventes):
    """Synthèse d'une période : total, totaux par client (du plus gros au plus petit), meilleur client."""
    totaux = total_par_client(ventes)
    tries = dict(sorted(totaux.items(), key=lambda kv: (-kv[1], kv[0])))
    return {"total": total_general(ventes), "clients": tries, "meilleur": meilleur_client(totaux)}
