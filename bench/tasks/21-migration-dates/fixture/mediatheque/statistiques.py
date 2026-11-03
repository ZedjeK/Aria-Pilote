"""Statistiques de fréquentation."""

from collections import Counter


def par_mois(emprunts):
    """Nombre d'emprunts par mois, clés « AAAA-MM » triées."""
    mois = Counter(f"{e.date_emprunt[6:10]}-{e.date_emprunt[3:5]}" for e in emprunts)
    return dict(sorted(mois.items()))


def par_type(emprunts, documents):
    return dict(Counter(documents[e.cote].type for e in emprunts).most_common())


def lecteurs_assidus(emprunts, minimum=3):
    compte = Counter(e.adherent for e in emprunts)
    return sorted(numero for numero, n in compte.items() if n >= minimum)
