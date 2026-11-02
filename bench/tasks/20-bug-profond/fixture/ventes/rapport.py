from ventes.filtres import du_mois
from ventes.formats import euros
from ventes.synthese import synthese


def rapport_mensuel(ventes, mois):
    s = synthese(du_mois(ventes, mois))
    lignes = [f"Ventes {mois} : {euros(s['total'])}"]
    lignes += [f"  {client} : {euros(montant)}" for client, montant in s["clients"].items()]
    lignes.append(f"Meilleur client : {s['meilleur']}")
    return "\n".join(lignes)


def rapport_trimestre(ventes, mois_liste):
    return "\n\n".join(rapport_mensuel(ventes, mois) for mois in mois_liste)
