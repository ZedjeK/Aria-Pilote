---
name: message-commit
description: Préparer un message de commit Git clair à partir des modifications en cours du dépôt.
---

# Message de commit

1. Regarde l'état du dépôt : `git status --short`, puis `git diff --stat` et `git diff --cached --stat`.
2. Lis les changements utiles (`git diff`, `git diff --cached`). Sur un gros diff, lis fichier par fichier.
3. Rédige le message en français :
   - **Titre** : 72 caractères au plus, qui dit ce que fait le commit (« Ajoute la reprise des téléchargements », « Corrige le calcul de la taille totale »).
   - Une ligne vide.
   - **Corps** : une liste de tirets qui explique le **pourquoi** et les points importants, pas la liste des fichiers.
4. Si les changements mélangent plusieurs sujets, propose de les séparer en plusieurs commits, avec un message pour chacun.
5. Présente le message. Ne lance `git add` ni `git commit` que si l'utilisateur le demande explicitement.
