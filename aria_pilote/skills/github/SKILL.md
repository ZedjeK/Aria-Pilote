---
name: github
description: Auditer, prendre en main ou protéger un dépôt GitHub : branches, CI Actions, rulesets, Dependabot, secrets, fusion.
registre: professionnel
---
# GitHub

## Objectif
Un dépôt sûr et vérifié (protections, CI, secrets, dépendances), réglé un changement à la fois, avec son retour arrière.

## Ne pas l'utiliser si
- Pousser, ouvrir ou fusionner une pull request : github-pr. Une issue : github-issues. Une release : github-release.
- Un message de commit : message-commit. Un bug dans le code : dev.
- Un secret déjà exposé ou un incident : securite, rotation d'abord.

## Méthode
### Prendre un dépôt en main (lecture seule d'abord)
1. État des lieux : visibilité, rôle, solo ou équipe ; derniers commits ; PR et issues ouvertes, CI récente, protection de la branche par défaut, fusion automatique, Dependabot, secret scanning. Note cet état avant toute modification : c'est le retour arrière.
2. Propose ensuite une seule action, la plus utile et la moins risquée.
### Protéger la branche par défaut
1. Ni suppression ni force-push ; un check de CI obligatoire, au nom exact affiché sur la PR ; une revue à proportion de l'équipe. Préfère les rulesets, un changement à la fois, testé sur une PR brouillon.
2. Tags de version (v*) : un ruleset qui interdit de les supprimer ou de les déplacer.
### Sécuriser la CI (Actions)
1. Actions tierces épinglées à un SHA complet et tenues à jour par Dependabot ; permissions explicites, en lecture par défaut, l'écriture seulement au job qui écrit ; OIDC plutôt que des secrets longue durée.
2. Jamais un champ non fiable (titre de PR, nom de branche, message de commit) interpolé dans un run : passe par une variable d'environnement. pull_request_target et workflow_run n'exécutent jamais le code d'une PR externe avec des secrets.
### Dépendances et hygiène
1. Dependabot : les alertes de sécurité avant les mises à jour de confort ; lis le changelog avant une montée majeure.
2. À proposer, sans corriger d'office : .gitattributes pour les fins de ligne, SECURITY.md, aucun binaire lourd dans Git.
### Secrets
1. Secret scanning et push protection actifs. Secret déjà poussé : rotation d'abord, nettoyage ensuite (il reste compromis sur un dépôt public).

## Format de la réponse
Constat (dépôt, protections, CI) · Écarts classés par risque · Correctifs un par un, avec commande et retour arrière · Vérification (commande et sortie) · Non vérifié.

## Règles
### À faire toujours
- Écrire sur GitHub (commit, push, commentaire, PR, fusion, release, réglage) est une action : l'annoncer et attendre l'accord.
- Vérifier un réglage du dépôt (API ou page Settings) avant d'affirmer ce qu'il fait ; sinon, dire « non vérifié ».
- Relire ce qui part (git diff --staged) avant tout push vers un dépôt public : secrets, données personnelles, chemins locaux.
- Rédiger les textes publiés d'une voix neutre, sans emoji.
### À ne jamais faire
- Exiger un check qui n'existe pas, ou renommer un job exigé sans changer le ruleset : plus rien ne serait fusionnable.
- Activer la fusion automatique sur une branche sans check obligatoire.
- Modifier une protection sans avoir noté l'état d'avant, ou deux changements à la fois.

## Vérifier avant de répondre
- [ ] L'état d'avant est noté, chaque écrit sur GitHub a été annoncé et accepté.
- [ ] L'état relu après changement est comparé à celui d'avant.

## Exemple
> Demande : « Prends en main mon dépôt et dis-moi ce qui cloche »
> Réponse attendue (résumé) : un état des lieux en lecture seule, les écarts classés par risque, puis une seule action proposée avec son retour arrière.
