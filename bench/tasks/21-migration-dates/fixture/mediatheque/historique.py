"""Historique des emprunts d'un adhérent."""

from .dates import lire_date


def chronologique(emprunts):
    return sorted(emprunts, key=lambda e: lire_date(e.date_emprunt))


def historique(emprunts, adherent):
    """Emprunts de l'adhérent, du plus ancien au plus récent."""
    return chronologique([e for e in emprunts if e.adherent == adherent])


def dernier_emprunt(emprunts, adherent):
    liste = historique(emprunts, adherent)
    return liste[-1] if liste else None
