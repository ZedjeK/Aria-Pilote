"""Rapport mensuel pour l'équipe de la médiathèque."""

from .dates import ecrire_date
from .retards import en_retard, jours_de_retard
from .statistiques import par_mois, par_type


def rapport(emprunts, documents, jour):
    lignes = [f"Rapport du {ecrire_date(jour)}", "", "Emprunts par mois :"]
    lignes += [f"  {mois} : {n}" for mois, n in par_mois(emprunts).items()]
    lignes += ["", "Par type :"] + [f"  {t} : {n}" for t, n in par_type(emprunts, documents).items()]
    retards = en_retard(emprunts, documents, jour)
    lignes += ["", f"Retards : {len(retards)}"]
    lignes += [f"  {e.cote} ({documents[e.cote].titre}) : {jours_de_retard(e, documents[e.cote], jour)} j"
               for e in retards]
    return "\n".join(lignes)
