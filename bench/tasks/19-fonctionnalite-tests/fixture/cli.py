import argparse
import sys

from stock.depot import charger
from stock.rapport import rapport_texte

STOCK = "donnees/stock.csv"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Gestion de stock")
    parser.add_argument("--rapport", action="store_true", help="affiche le rapport du stock")
    args = parser.parse_args(argv)
    produits = charger(STOCK)
    if args.rapport:
        print(rapport_texte(produits))
    return 0


if __name__ == "__main__":
    sys.exit(main())
