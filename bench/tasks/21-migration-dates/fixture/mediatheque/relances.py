"""Courriels de relance pour les documents en retard."""

from .retards import en_retard, jours_de_retard

MODELE = """Bonjour {nom},

Le document « {titre} », emprunté le {date}, aurait dû être rendu il y a {jours} jour(s).
Merci de le rapporter à la médiathèque.
"""


def relance(emprunt, adherent, document, jour):
    return MODELE.format(nom=adherent.nom, titre=document.titre, date=emprunt.date_emprunt,
                         jours=jours_de_retard(emprunt, document, jour))


def relances(emprunts, adherents, documents, jour):
    """(email, texte) pour chaque emprunt en retard."""
    return [(adherents[e.adherent].email, relance(e, adherents[e.adherent], documents[e.cote], jour))
            for e in en_retard(emprunts, documents, jour)]
