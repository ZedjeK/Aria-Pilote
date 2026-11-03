# Médiathèque

- mediatheque/modele.py : adhérents, documents, emprunts
- mediatheque/dates.py : dates au format JJ/MM/AAAA, durées de prêt
- mediatheque/depot.py : lecture des CSV de donnees/
- mediatheque/emprunts.py : emprunter, rendre
- mediatheque/retards.py, relances.py : retards et courriels de relance
- mediatheque/statistiques.py, historique.py, rapport.py : suivi de la fréquentation
- mediatheque/export.py : export CSV
- cli.py : python cli.py rapport|relances|historique <adherent> [--date JJ/MM/AAAA]

Tests : python -m unittest
