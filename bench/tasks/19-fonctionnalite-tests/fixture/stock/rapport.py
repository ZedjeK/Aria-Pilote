def rapport_texte(produits):
    """Une ligne par produit, puis la valeur totale du stock."""
    lignes = [f"{p.ref:<6} {p.nom:<20} {p.quantite:>5}" for p in produits]
    total = round(sum(p.valeur for p in produits), 2)
    lignes.append(f"Valeur du stock : {total:.2f} €")
    return "\n".join(lignes)
