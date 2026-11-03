"""Ligne de commande : python cli.py rapport|relances|historique <adherent> [--date JJ/MM/AAAA]"""

import sys

from mediatheque import depot
from mediatheque.dates import aujourd_hui, ecrire_date, lire_date
from mediatheque.historique import historique
from mediatheque.rapport import rapport
from mediatheque.relances import relances


def main(argv):
    jour = aujourd_hui()
    if "--date" in argv:
        i = argv.index("--date")
        jour = lire_date(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    if not argv:
        print(__doc__)
        return 2
    emprunts, documents = depot.charger_emprunts(), depot.charger_documents()
    if argv[0] == "rapport":
        print(rapport(emprunts, documents, jour))
    elif argv[0] == "relances":
        for email, texte in relances(emprunts, depot.charger_adherents(), documents, jour):
            print(f"À : {email}\n{texte}")
    elif argv[0] == "historique" and len(argv) > 1:
        for e in historique(emprunts, argv[1]):
            rendu = ecrire_date(lire_date(e.date_retour)) if e.date_retour else "en cours"
            print(f"{e.date_emprunt}  {documents[e.cote].titre}  ({rendu})")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
