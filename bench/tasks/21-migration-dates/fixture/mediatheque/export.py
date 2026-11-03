"""Export des emprunts en CSV, au format de donnees/emprunts.csv."""

import csv

COLONNES = ["cote", "adherent", "date_emprunt", "date_retour"]


def exporter(emprunts, chemin):
    with open(chemin, "w", encoding="utf-8", newline="") as f:
        ecrivain = csv.writer(f, delimiter=";")
        ecrivain.writerow(COLONNES)
        for e in emprunts:
            ecrivain.writerow([e.cote, e.adherent, e.date_emprunt, e.date_retour or ""])
