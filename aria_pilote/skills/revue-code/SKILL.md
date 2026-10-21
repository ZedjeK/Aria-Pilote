---
name: revue-code
description: Relire du code ou un script pour trouver bugs, failles de sécurité et points fragiles, avec un correctif proposé pour chacun.
---

# Revue de code

1. Lis en entier les fichiers concernés (`read_file`) avant de juger. Si la demande vise un dossier, repère d'abord les fichiers utiles (`find_files`, `grep`).
2. Cherche dans cet ordre :
   - **Bugs** : cas limites (vide, nul, très grand), conditions inversées, erreurs non gérées, mauvaise portée de variable.
   - **Sécurité** : injections (commande, SQL, chemin), secrets en clair, entrées non validées, droits trop larges, téléchargement puis exécution.
   - **Robustesse** : ressources non libérées, absence de délai d'attente, effets de bord, dépendance à l'environnement.
   - **Lisibilité** : seulement ce qui gêne vraiment la compréhension.
3. Pour chaque problème, donne : `fichier:ligne`, gravité (**critique**, **importante** ou **mineure**), le problème en une phrase, et le correctif dans un bloc de code.
4. Classe du plus grave au moins grave. Si le code est sain, dis-le simplement : n'invente pas de problème.
5. Ne modifie aucun fichier : propose. L'utilisateur décide ensuite de ce que tu appliques.
