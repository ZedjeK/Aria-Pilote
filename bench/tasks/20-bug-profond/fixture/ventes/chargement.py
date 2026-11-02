import csv

from ventes.formats import parse_montant


def lire_ventes(chemin):
    """Ventes du fichier CSV (date;client;montant), la date au format AAAA-MM-JJ."""
    with open(chemin, encoding="utf-8", newline="") as f:
        return [{"date": l["date"], "mois": l["date"][:7], "client": l["client"].strip(),
                 "montant": parse_montant(l["montant"])} for l in csv.DictReader(f, delimiter=";")]
