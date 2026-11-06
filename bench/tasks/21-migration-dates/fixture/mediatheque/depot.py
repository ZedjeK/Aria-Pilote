"""Lecture des fichiers CSV de donnees/."""

import csv
from pathlib import Path

from .modele import Adherent, Document, Emprunt

DONNEES = Path(__file__).resolve().parent.parent / "donnees"


def _lignes(nom, dossier=None):
    with open(Path(dossier or DONNEES) / nom, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def charger_adherents(dossier=None):
    return {l["numero"]: Adherent(l["numero"], l["nom"], l["email"]) for l in _lignes("adherents.csv", dossier)}


def charger_documents(dossier=None):
    return {l["cote"]: Document(l["cote"], l["titre"], l["type"]) for l in _lignes("documents.csv", dossier)}


def charger_emprunts(dossier=None):
    return [Emprunt(l["cote"], l["adherent"], l["date_emprunt"], l["date_retour"] or None)
            for l in _lignes("emprunts.csv", dossier)]
