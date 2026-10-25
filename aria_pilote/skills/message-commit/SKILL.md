---
name: message-commit
description: Préparer un message de commit Git clair à partir des modifications en cours du dépôt.
registre: professionnel
---
# Message de commit

## Objectif
Un message de commit qui dit ce que fait le commit et pourquoi.

## Ne pas l'utiliser si
- Expliquer le fonctionnement de Git : prof.

## Méthode
1. Regarde l'état du dépôt : git status --short, puis git diff --stat et git diff --cached --stat.
2. Lis les changements utiles (git diff, git diff --cached) ; sur un gros diff, fichier par fichier.
3. Si les changements mélangent plusieurs sujets, propose de les séparer en plusieurs commits, avec un message pour chacun.

## Format de la réponse
En français : un titre de 72 caractères au plus qui dit ce que fait le commit (« Ajoute la reprise des téléchargements »), une ligne vide, puis un corps en liste de tirets qui explique le pourquoi et les points importants, pas la liste des fichiers.

## Règles
### À faire toujours
- Présenter le message pour validation.
### À ne jamais faire
- Lancer git add ou git commit sans que l'utilisateur le demande explicitement.

## Vérifier avant de répondre
- [ ] Le titre tient en 72 caractères et dit ce que fait le commit.
- [ ] Le corps explique le pourquoi.
