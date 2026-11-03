"""Emprunter et rendre un document."""

from .modele import Emprunt


class DejaEmprunte(Exception):
    pass


def en_cours(emprunts, cote):
    return next((e for e in emprunts if e.cote == cote and e.date_retour is None), None)


def emprunter(emprunts, cote, adherent, jour):
    """Enregistre un emprunt ; `jour` est la date d'emprunt."""
    if en_cours(emprunts, cote):
        raise DejaEmprunte(cote)
    emprunt = Emprunt(cote, adherent, jour)
    emprunts.append(emprunt)
    return emprunt


def rendre(emprunts, cote, jour):
    """Enregistre le retour d'un document ; `jour` est la date de retour."""
    emprunt = en_cours(emprunts, cote)
    if emprunt is None:
        raise KeyError(cote)
    emprunt.date_retour = jour
    return emprunt
