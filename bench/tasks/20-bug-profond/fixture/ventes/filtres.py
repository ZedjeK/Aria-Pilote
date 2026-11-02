def du_mois(ventes, mois):
    """Ventes d'un mois donné (« 2026-02 »)."""
    return [v for v in ventes if v["mois"] == mois]


def du_client(ventes, client):
    return [v for v in ventes if v["client"].lower() == client.lower()]
