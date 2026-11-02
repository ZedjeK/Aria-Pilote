from outils.montants import calcul_ttc

FRAIS_HT = 4.90
SEUIL_GRATUIT = 50


def frais_livraison(total_ttc):
    """Livraison offerte à partir de 50 € TTC."""
    return 0.0 if total_ttc >= SEUIL_GRATUIT else calcul_ttc(FRAIS_HT, 0.20)
