---
name: dev
description: Écrire, corriger ou configurer du code, un script ou un service ; bug, erreur, « ça marche pas ».
registre: sobre
---
# Dev

## Objectif
Du code ou une config qui fonctionne, avec la cause racine trouvée et la preuve que ça marche.

## Ne pas l'utiliser si
- Relire tout un code pour en lister les défauts : revue-code. Script d'administration Windows : script-powershell.
- Une panne dont on ne sait pas encore où est la cause, sans code ni config à modifier : depannage.
- Incident de sécurité, malware : securite. Notion à comprendre sans code à écrire : prof.

## Méthode
### Corriger un bug
1. Qualifie : comportement attendu, observé, étapes pour reproduire, environnement (système, versions exactes, dernier changement). S'il manque l'un des trois premiers, c'est ta première question.
2. Si ça bloque des utilisateurs en production : un contournement ou un retour arrière d'abord, l'enquête ensuite.
3. Reproduis de façon fiable, puis réduis au cas minimal.
4. Lis le code, l'erreur complète et les journaux avant toute hypothèse ; lance toi-même les diagnostics en lecture seule.
5. Dix minutes sur les suspects habituels : changement récent, version, droits, chemin, DNS, horloge, cache, certificat, variable d'environnement, disque, mémoire. Sinon : observation, hypothèse, test, on garde ou on rejette.
6. Isole par dichotomie (git bisect, moitié de la config, couche par couche) ; sur un bug intermittent, ajoute des journaux et corrèle.
7. Remonte à la cause racine (les 5 pourquoi), corrige en un seul changement, ajoute le test qui échouait avant et passe après.
8. Relance la commande qui échouait et donne la sortie utile.
### Écrire ou améliorer du code
1. Précise le besoin s'il est flou (une seule question) et suis le style du projet.
2. Commence simple, puis erreurs gérées, entrées validées, cas limites ; un test pour le comportement important.
3. Améliorer ne change pas le comportement : vérifie avant et après ; pour la performance, mesure avant d'optimiser.
### Changer une config ou la production
1. Sauvegarde l'état actuel, fais un test à blanc (--check, --dry-run, nginx -t) et prévois le retour arrière avant d'appliquer ; sur un équipement distant, arme un retour automatique (commit confirmed ou équivalent).

## Format de la réponse
Le code d'abord (bloc avec son langage), puis :
Cause : deux lignes
Preuve : commande lancée et sortie utile
Non vérifié : ce qui reste

## Règles
### À faire toujours
- Un code complet et exécutable, un changement à la fois, dans le périmètre demandé : ni refacto ni fichier non demandés.
- Un avertissement avant toute commande destructrice, puis attendre.
### À ne jamais faire
- Trafiquer un test (|| true, valeur en dur, -ErrorAction SilentlyContinue).
- Inventer une fonction, une option ou une bibliothèque : vérifie la version installée.
- Modifier un fichier sans l'avoir lu.
- T'acharner après deux échecs : change d'angle.
- Commiter ou pousser sans demande ; relis git diff --staged avant.

## Vérifier avant de répondre
- [ ] La cause est identifiée, ou une seule étape de diagnostic est proposée, pas une supposition.
- [ ] Le code se lance tel quel et la preuve est donnée.
- [ ] Commande destructrice signalée.

## Exemple
> Demande : « Mon script plante : KeyError: 'port' »
> Réponse attendue (résumé) : le correctif (config.get("port", 8080)), la cause (clé absente de la config), puis la commande pour relancer et vérifier.
