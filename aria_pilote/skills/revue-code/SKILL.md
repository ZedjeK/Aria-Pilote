---
name: revue-code
description: Relire du code ou un script pour trouver bugs, failles de sécurité et points fragiles, avec un correctif proposé pour chacun.
registre: professionnel
---
# Revue de code

## Objectif
La liste des problèmes réels, du plus grave au moins grave, chacun avec son correctif.

## Ne pas l'utiliser si
- Corriger un bug précis déjà identifié : dev.
- Écrire du code neuf : dev.

## Méthode
1. Lis en entier les fichiers concernés (read_file) avant de juger ; pour un dossier, repère d'abord les fichiers utiles (find_files, grep).
2. Cherche les bugs : cas limites (vide, nul, très grand), conditions inversées, erreurs non gérées, mauvaise portée de variable.
3. Cherche les failles : injections (commande, SQL, chemin), secrets en clair, entrées non validées, droits trop larges, téléchargement puis exécution.
4. Cherche la fragilité : ressources non libérées, absence de délai d'attente, effets de bord, dépendance à l'environnement.
5. Note la lisibilité seulement quand elle gêne vraiment la compréhension.

## Format de la réponse
Pour chaque problème : fichier:ligne, gravité (critique, importante ou mineure), le problème en une phrase, puis le correctif dans un bloc de code. Du plus grave au moins grave.

## Règles
### À faire toujours
- Proposer : l'utilisateur décide de ce qui est appliqué.
### À ne jamais faire
- Modifier un fichier pendant la revue.
- Inventer un problème : si le code est sain, le dire simplement.

## Vérifier avant de répondre
- [ ] Chaque problème a son emplacement, sa gravité et son correctif.
- [ ] Le classement va du plus grave au moins grave.
