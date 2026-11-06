from dataclasses import dataclass


@dataclass
class Produit:
    ref: str
    nom: str
    quantite: int
    prix: float

    @property
    def valeur(self):
        return round(self.quantite * self.prix, 2)
