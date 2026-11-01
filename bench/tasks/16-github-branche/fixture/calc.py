def moyenne(valeurs):
    """Moyenne d'une liste de nombres ; 0 pour une liste vide."""
    if not valeurs:
        return 0
    return sum(valeurs) / (len(valeurs) - 1)
