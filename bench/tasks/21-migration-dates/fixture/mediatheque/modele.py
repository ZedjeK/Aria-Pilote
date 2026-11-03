"""Objets de la médiathèque."""

from dataclasses import dataclass


@dataclass
class Adherent:
    numero: str
    nom: str
    email: str


@dataclass
class Document:
    cote: str
    titre: str
    type: str  # livre, dvd, bd


@dataclass
class Emprunt:
    cote: str
    adherent: str
    date_emprunt: str  # « JJ/MM/AAAA »
    date_retour: str | None = None  # « JJ/MM/AAAA », None tant que le document n'est pas rendu
