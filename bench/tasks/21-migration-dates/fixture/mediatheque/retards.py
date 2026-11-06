"""Retards : un document non rendu au-delà de sa durée de prêt."""

from .dates import DUREE_PRET, jours_entre, lire_date


def jours_de_retard(emprunt, document, jour):
    """Jours de retard à la date `jour` (0 si le document est dans les temps ou déjà rendu)."""
    if emprunt.date_retour is not None:
        return 0
    ecoules = jours_entre(lire_date(emprunt.date_emprunt), jour)
    return max(0, ecoules - DUREE_PRET[document.type])


def en_retard(emprunts, documents, jour):
    """Emprunts en retard à la date `jour`, du plus en retard au moins en retard."""
    retards = [(jours_de_retard(e, documents[e.cote], jour), e) for e in emprunts]
    return [e for n, e in sorted(retards, key=lambda r: -r[0]) if n > 0]
