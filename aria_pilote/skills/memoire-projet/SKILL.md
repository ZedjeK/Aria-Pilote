---
name: memoire-projet
description: Créer ou mettre à jour le MEMOIRE.md d'un dossier attaché : ce que l'agent doit savoir du projet à chaque tâche (langage, structure, commandes, conventions).
registre: sobre
---
# Mémoire du projet

## Objectif
Un MEMOIRE.md court et exact, qui fait gagner du temps à chaque tâche suivante.

## Ne pas l'utiliser si
- Aucun dossier attaché : dis à l'utilisateur d'en attacher un. Organiser ou planifier un projet : projet.
- Documenter le projet pour des humains (README, guide) : redaction.

## Méthode
### Explorer le dossier
1. Repère la structure (list_dir, find_files) et les fichiers qui disent l'essentiel : README, fichiers de dépendances (requirements.txt, package.json, pyproject.toml, .csproj), configuration de build et de test.
2. Lis-les plutôt que deviner ; pour un gros projet, délègue l'exploration à l'explorateur s'il est disponible.
3. Relève les commandes réelles pour installer, lancer, tester et construire : celles qui figurent dans les fichiers, pas des suppositions.
### Écrire la mémoire
1. Des sections courtes : le projet en deux phrases ; la structure (dossiers clés) ; les commandes ; les conventions (style, nommage, langue des commentaires) ; les pièges connus et ce qu'il ne faut pas toucher.
2. Seulement ce qui sert à chaque tâche : ni historique, ni liste complète des fichiers.
3. Ce que tu n'as pas pu vérifier : marque-le [à confirmer], ou pose la question.
### Mettre à jour une mémoire existante
1. Lis le MEMOIRE.md actuel (ou l'ancien ARIA.md) et garde ce que l'utilisateur y a écrit ; corrige ce qui est périmé, ajoute ce qui manque.
2. Montre les changements avant d'écrire.

## Bonnes pratiques
- Viser une page, environ 100 lignes : elle est relue à chaque tâche, et chaque ligne coûte des tokens (au-delà de 20 000 caractères, la suite est coupée).
- Des puces, et les commandes dans des blocs de code.
- Aucun secret dans la mémoire : ni mot de passe, ni clé, ni jeton.

## Format de la réponse
Le contenu proposé du MEMOIRE.md dans un bloc markdown, puis son écriture à la racine du dossier, avec l'accord de l'utilisateur.

## Règles
### À faire toujours
- Des commandes vérifiées dans les fichiers du projet.
- Garder ce que l'utilisateur a écrit lui-même.
### À ne jamais faire
- Inventer une commande ou une convention.
- Recopier du code ou un secret.
- Écraser un MEMOIRE.md existant sans montrer les changements.

## Vérifier avant de répondre
- [ ] Chaque commande vient d'un fichier du projet, ou est marquée [à confirmer].
- [ ] La mémoire tient en une page.
- [ ] Aucun secret n'y figure.

## Exemple
> Demande : « Prépare la mémoire de ce projet. » (un dossier Python avec requirements.txt et tests/)
> Réponse attendue (résumé) : l'exploration, puis un MEMOIRE.md avec le projet en deux phrases, py -m pip install -r requirements.txt, py -m unittest discover tests, les conventions relevées (PEP 8, commentaires en français) et un point [à confirmer] sur la commande de lancement.
