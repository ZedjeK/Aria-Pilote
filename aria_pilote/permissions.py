"""Règles « toujours autoriser » pour les commandes de l'agent, par dossier de projet.

Une règle est une commande exacte (« git status ») ou un début de commande terminé par * (« python -m unittest* »).
Elles sont stockées côté Aria (config.json), jamais dans le projet : un dépôt cloné ne peut pas arriver
avec ses propres règles. Une règle « commence par » ne couvre pas une suite qui contient un opérateur
PowerShell (« ; », « | », « > », « $(…) »…) : sinon « python -m unittest; Remove-Item … » passerait.
La liste noire et les hooks restent prioritaires (ils interviennent avant toute règle).
"""

import re
from pathlib import Path

from . import config

# Tout ce qui permet d'enchaîner, de rediriger ou d'évaluer une autre commande
OPERATORS = re.compile(r"[;&|<>`$(){}\r\n]")
MAX_RULES = 100


class RuleError(Exception):
    pass


def normalize(command):
    """PowerShell ignore la casse, et les espaces multiples ne changent rien."""
    return " ".join(str(command).split()).lower()


def _key(root):
    return str(Path(root).resolve()).lower()


def rules(root):
    return list(config.get().get("command_rules", {}).get(_key(root), []))


def matches(rule, command):
    # Un retour à la ligne sépare deux commandes, et la normalisation le changerait en simple espace :
    # une commande sur plusieurs lignes ne correspond donc jamais à une règle (on redemande)
    if "\n" in str(command) or "\r" in str(command):
        return False
    cmd, rule = normalize(command), normalize(rule)
    if not rule.endswith("*"):
        return cmd == rule
    prefix = rule[:-1].rstrip()
    if not prefix or not cmd.startswith(prefix):
        return False
    rest = cmd[len(prefix):]
    # « python -m unittest* » ne couvre pas « python -m unittest_maison » : la règle s'arrête à un mot entier
    if rest and prefix[-1].isalnum() and not rest.startswith(" "):
        return False
    return not OPERATORS.search(rest)


def find(root, command):
    """La règle du projet qui autorise cette commande, ou None."""
    return next((r for r in rules(root) if matches(r, command)), None)


def suggest_prefix(command):
    """Début de commande proposé pour une règle « commence par » (modifiable avant d'être enregistré)."""
    head = OPERATORS.split(command, 1)[0]
    tokens = head.split()
    if not tokens:
        return None
    # Les 3 premiers mots au plus, sans les options en fin : « python -m unittest test_stats » et
    # « python -m unittest » → « python -m unittest* » ; « git log -5 » → « git log* »
    kept = tokens[:3]
    while len(kept) > 1 and kept[-1].startswith("-"):
        kept.pop()
    return " ".join(kept) + "*"


def add(root, rule, command):
    """Enregistre une règle ; elle doit couvrir la commande en attente (pas de règle arbitraire)."""
    rule = normalize(rule)
    if not rule or rule == "*":
        raise RuleError("Règle vide.")
    if "*" in rule[:-1]:
        raise RuleError("L'étoile n'est permise qu'à la fin de la règle.")
    if rule.endswith("*") and OPERATORS.search(rule):
        raise RuleError("Une règle « commence par » ne peut pas contenir d'opérateur (; & | > $ …).")
    if not matches(rule, command):
        raise RuleError("Cette règle ne couvre pas la commande en attente.")
    current = rules(root)
    if rule not in current:
        if len(current) >= MAX_RULES:
            raise RuleError(f"{MAX_RULES} règles au plus par projet.")
        config.update({"command_rules": {_key(root): current + [rule]}})
    return rule


def remove(root, rule):
    current = rules(root)
    if normalize(rule) not in current:
        raise RuleError("Règle introuvable.")
    config.update({"command_rules": {_key(root): [r for r in current if r != normalize(rule)]}})
