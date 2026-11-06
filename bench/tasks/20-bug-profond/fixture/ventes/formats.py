def parse_montant(texte):
    """« 1 234,50 » -> 1234.5 : espaces (y compris insécables) des milliers retirés, virgule décimale."""
    propre = texte.replace(" ", "").replace("\xa0", "").replace(" ", "")
    return float(propre.replace(",", "."))


def euros(montant):
    """1234.5 -> « 1 234,50 € »"""
    return f"{montant:,.2f} €".replace(",", " ").replace(".", ",")
