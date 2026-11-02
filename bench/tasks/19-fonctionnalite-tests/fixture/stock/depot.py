import csv

from stock.modele import Produit


def charger(chemin):
    """Produits du fichier CSV (ref;nom;quantite;prix), dans l'ordre du fichier."""
    with open(chemin, encoding="utf-8", newline="") as f:
        return [Produit(l["ref"], l["nom"], int(l["quantite"]), float(l["prix"]))
                for l in csv.DictReader(f, delimiter=";")]
