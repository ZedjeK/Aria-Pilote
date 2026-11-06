class StockInsuffisant(Exception):
    pass


def entree(produit, quantite):
    produit.quantite += quantite


def sortie(produit, quantite):
    if quantite > produit.quantite:
        raise StockInsuffisant(f"{produit.ref} : {produit.quantite} en stock, {quantite} demandés")
    produit.quantite -= quantite
