---
name: github
description: Auditer, prendre en main ou protéger un dépôt GitHub : branches, CI Actions, rulesets, Dependabot, secrets, fusion.
registre: professionnel
---
# GitHub

## Objectif
Un dépôt sûr et vérifié (protections, CI, secrets, dépendances), réglé un changement à la fois, avec son retour arrière.

## Ne pas l'utiliser si
- Rédiger ou ouvrir une pull request : github-pr. Une issue : github-issues. Une release : github-release.
- Un message de commit : message-commit. Un bug dans le code : dev.
- Un secret déjà exposé ou un incident : securite, rotation d'abord.

## Méthode
### Prendre un dépôt en main (lecture seule d'abord)
1. État des lieux : visibilité, rôle, solo ou équipe ; derniers commits ; PR et issues ouvertes, CI récente, protection de la branche par défaut, fusion automatique, Dependabot, secret scanning. Note cet état avant toute modification : c'est le retour arrière.
2. Propose ensuite une seule action, la plus utile et la moins risquée.
### Protéger la branche par défaut
1. Ni suppression ni force-push ; un check de CI obligatoire ; une revue obligatoire à proportion de l'équipe. Préfère les rulesets, un changement à la fois.
### Sécuriser la CI (Actions)
1. Actions tierces épinglées à un SHA complet et tenues à jour par Dependabot ; permissions explicites, en lecture par défaut ; OIDC plutôt que des secrets longue durée.
2. Jamais un champ non fiable (titre de PR, nom de branche, message de commit) interpolé dans un run : passe par une variable d'environnement. pull_request_target et workflow_run n'exécutent jamais le code d'une PR externe avec des secrets.
### Fusionner
1. Lis l'état de la CI une seule fois, quand l'utilisateur dit de fusionner. Verte et propre : fusionne avec le SHA lu. En cours ou rouge : ne fusionne pas, dis ce qui reste ou quel job échoue.
2. Plusieurs PR : une à la fois, en relisant l'état après chaque fusion. Fusion automatique seulement sur demande, si le dépôt l'autorise et si un check est obligatoire.
### Secrets et historique
1. Secret scanning et push protection actifs. Secret déjà poussé : rotation d'abord, nettoyage ensuite (il reste compromis sur un dépôt public).
2. Aucune réécriture d'historique sur une branche partagée ou protégée ; ailleurs, branche de sauvegarde d'abord et --force-with-lease.

## Format de la réponse
Constat (dépôt, protections, CI) · Écarts classés par risque · Correctifs un par un, avec commande et retour arrière · Vérification (commande et sortie) · Non vérifié.

## Règles
### À faire toujours
- Écrire sur GitHub (commit, push, commentaire, PR, fusion, release, réglage) est une action : l'annoncer et attendre l'accord.
- Vérifier un réglage du dépôt (API ou page Settings) avant d'affirmer ce qu'il fait ; sinon, dire « non vérifié ».
- Relire ce qui part (git diff --staged) avant tout push vers un dépôt public : secrets, données personnelles, chemins locaux.
- Rédiger les textes publiés d'une voix neutre, sans emoji.
### À ne jamais faire
- Fusionner sans CI verte lue juste avant, ni sans le SHA lu.
- Activer la fusion automatique sur une branche sans check obligatoire.
- Modifier une protection sans avoir noté l'état d'avant, ou deux changements à la fois.

## Vérifier avant de répondre
- [ ] L'état d'avant est noté, chaque écrit sur GitHub a été annoncé et accepté.
- [ ] L'état relu après changement est comparé à celui d'avant.

## Exemple
> Demande : « Prends en main mon dépôt et dis-moi ce qui cloche »
> Réponse attendue (résumé) : un état des lieux, les écarts classés par risque, puis une seule action proposée avec son retour arrière.
