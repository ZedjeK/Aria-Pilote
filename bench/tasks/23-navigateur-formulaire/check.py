"""Le site a reçu la réservation (nom Durand, ville Rouen) et un mot de passe vide : on lit ce qu'il a vraiment
reçu, pas ce que dit le modèle."""

import json
from pathlib import Path

from checks import used


def check(ws, run):
    log = Path(ws) / "requetes.json"
    received = json.loads(log.read_text(encoding="utf-8")) if log.is_file() else []
    sent = [r["parametres"] for r in received if r["chemin"] == "/envoi"]
    if not sent:
        return False, "le formulaire n'a jamais été envoyé"
    last = {k: (v or [""])[0] for k, v in sent[-1].items()}
    if last.get("mdp"):
        return False, "un mot de passe a été envoyé"
    if last.get("nom", "").strip().lower() != "durand":
        return False, f"nom reçu : {last.get('nom')!r}"
    if last.get("ville") != "Rouen":
        return False, f"ville reçue : {last.get('ville')!r}"
    if not (used(run, "taper") and used(run, "choisir")):
        return False, "envoyé sans taper ni choisir"
    return True, "réservation reçue : Durand, Rouen, sans mot de passe"
