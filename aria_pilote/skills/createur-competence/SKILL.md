---
name: createur-competence
description: Créer ou améliorer une compétence d'Aria Pilote (un SKILL.md) pour une tâche que l'utilisateur veut rendre répétable.
registre: sobre
---
# Créateur de compétence

## Objectif
Une compétence prête à enregistrer, au format du modèle, qui se déclenche quand il faut, et seulement là.

## Ne pas l'utiliser si
- L'utilisateur veut juste faire la tâche une fois : prends la compétence de la tâche (dev, redaction…).
- Une petite retouche sur une compétence existante : indique Réglages › Compétences, son formulaire suffit.

## Méthode
### Comprendre le besoin
1. Fais décrire la tâche à rendre répétable, avec deux ou trois exemples réels de demandes (une question à la fois).
2. Vérifie qu'aucune compétence existante ne la couvre déjà ; si c'est le cas, propose plutôt de la personnaliser.
### Écrire la compétence
1. Un nom court, en minuscules avec des tirets, qui ne ressemble pas à celui d'une compétence existante.
2. « Quand la charger » en une phrase, avec les mots que l'utilisateur emploie vraiment.
3. L'objectif en une phrase, puis « Ne pas l'utiliser si » : les cas voisins, et la compétence à prendre à la place.
4. La méthode en étapes vérifiables (par cas si besoin), le format de la réponse, les règles (à faire toujours, à ne jamais faire), les vérifications et un exemple.
5. Le registre suggéré : naturel, sobre ou professionnel, selon le ton de la tâche.
### Tester le déclenchement
1. Propose quatre demandes qui doivent la charger, et deux demandes voisines qui ne doivent pas la charger, à ajouter au test de sélection.

## Format de la réponse
Le SKILL.md complet dans un bloc de code, prêt à coller dans Réglages › Compétences › Nouvelle › Mode texte, puis les phrases de test.

## Règles
### À faire toujours
- Respecter exactement les titres du modèle, sinon le formulaire ne pourra pas relire le fichier.
- Une description qui la distingue de ses voisines.
### À ne jamais faire
- Une compétence fourre-tout qui couvre plusieurs métiers.
- Une étape que l'utilisateur n'a pas validée.

## Vérifier avant de répondre
- [ ] Les titres sont conformes au modèle.
- [ ] Le nom et la description ne se confondent avec aucune compétence existante.
- [ ] « Ne pas l'utiliser si » contient au moins un cas.

## Exemple
> Demande : « Fais-moi une compétence pour écrire mes playbooks Ansible. »
> Réponse attendue (résumé) : deux questions sur ses habitudes, puis le SKILL.md « playbook-ansible » (idempotence, --check avant d'appliquer, variables dans des fichiers dédiés) et six phrases de test.
