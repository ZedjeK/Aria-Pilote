"""Dates au format français."""

from datetime import date, datetime

FORMAT = "%d/%m/%Y"
DUREE_PRET = {"livre": 21, "bd": 21, "dvd": 7}  # en jours


def lire_date(texte):
    """« JJ/MM/AAAA » → date."""
    return datetime.strptime(texte.strip(), FORMAT).date()


def ecrire_date(jour):
    """date → « JJ/MM/AAAA »."""
    return jour.strftime(FORMAT)


def jours_entre(debut, fin):
    return (fin - debut).days


def aujourd_hui():
    return date.today()
