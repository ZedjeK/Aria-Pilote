import contextlib
import csv
import io
import tempfile
import unittest
from datetime import date
from pathlib import Path

import cli
from mediatheque import depot
from mediatheque.export import exporter
from mediatheque.historique import dernier_emprunt, historique
from mediatheque.rapport import rapport
from mediatheque.relances import relances
from mediatheque.retards import en_retard, jours_de_retard
from mediatheque.statistiques import lecteurs_assidus, par_mois, par_type

JOUR = date(2026, 11, 15)


class Mediatheque(unittest.TestCase):
    def setUp(self):
        self.emprunts = depot.charger_emprunts()
        self.documents = depot.charger_documents()
        self.adherents = depot.charger_adherents()

    def test_retards(self):
        retards = en_retard(self.emprunts, self.documents, JOUR)
        self.assertEqual([e.cote for e in retards], ["B001", "D002", "D001"])
        self.assertEqual([jours_de_retard(e, self.documents[e.cote], JOUR) for e in retards], [20, 7, 2])

    def test_relances(self):
        email, texte = relances(self.emprunts, self.adherents, self.documents, JOUR)[0]
        self.assertEqual(email, "jeremy@exemple.fr")
        self.assertIn("« Akira », emprunté le 05/10/2026, aurait dû être rendu il y a 20 jour(s)", texte)

    def test_export_identique_aux_donnees(self):
        with tempfile.TemporaryDirectory() as dossier:
            chemin = Path(dossier) / "export.csv"
            exporter(self.emprunts, chemin)
            with open(chemin, encoding="utf-8", newline="") as f:
                exporte = list(csv.reader(f, delimiter=";"))
        with open(depot.DONNEES / "emprunts.csv", encoding="utf-8", newline="") as f:
            self.assertEqual(exporte, list(csv.reader(f, delimiter=";")))

    def test_statistiques(self):
        self.assertEqual(par_mois(self.emprunts), {"2026-09": 2, "2026-10": 3, "2026-11": 3})
        self.assertEqual(par_type(self.emprunts, self.documents), {"livre": 4, "dvd": 3, "bd": 1})
        self.assertEqual(lecteurs_assidus(self.emprunts), ["A01"])

    def test_historique(self):
        self.assertEqual([e.cote for e in historique(self.emprunts, "A01")], ["L001", "B001", "L001", "L003"])
        self.assertEqual(dernier_emprunt(self.emprunts, "A01").cote, "L003")

    def test_rapport(self):
        texte = rapport(self.emprunts, self.documents, JOUR)
        for ligne in ("Rapport du 15/11/2026", "  2026-10 : 3", "Retards : 3", "  B001 (Akira) : 20 j"):
            self.assertIn(ligne, texte.splitlines())

    def test_ligne_de_commande(self):
        sortie = io.StringIO()
        with contextlib.redirect_stdout(sortie):
            code = cli.main(["historique", "A01", "--date", "15/11/2026"])
        lignes = sortie.getvalue().splitlines()
        self.assertEqual(code, 0)
        self.assertEqual(lignes[0], "02/09/2026  Fondation  (20/09/2026)")
        self.assertEqual(lignes[-1], "03/11/2026  Neuromancien  (en cours)")


if __name__ == "__main__":
    unittest.main()
