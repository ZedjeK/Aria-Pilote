---
name: github-issues
description: Lire, chercher, créer, commenter et trier les issues d'un dépôt GitHub avec gh.
registre: professionnel
---
# Issues GitHub

## Objectif
Des issues claires et à jour : retrouvées vite, bien décrites, étiquetées, et fermées avec leur raison.

## Ne pas l'utiliser si
- Écrire le correctif d'une issue : dev, puis github-pr pour le livrer.
- Diagnostiquer une panne sur la machine : depannage.

## Méthode
### Lire et chercher
1. gh issue list, filtré au besoin : --state open, --label bug, --assignee "@me", --search "mots". Dans plusieurs dépôts : gh search issues "mots".
2. Le détail et la discussion : gh issue view <n> --comments.
### Créer
1. Cherche d'abord un doublon : gh issue list --state all --search "mots clés".
2. Rédige dans .git/ISSUE_BODY.md (write_file) : contexte, étapes pour reproduire, attendu, obtenu, environnement (version, système). Un titre court qui dit le symptôme.
3. Présente le texte, puis gh issue create --title "…" --body-file .git/ISSUE_BODY.md ; --label seulement avec une étiquette qui existe (gh label list).
### Trier
1. Pour chaque issue ouverte : type (bug, amélioration, question), doublon éventuel, priorité si le dépôt en a.
2. Présente les changements proposés en liste, et applique seulement ceux que l'utilisateur valide : gh issue edit <n> --add-label …, gh issue close <n> --reason "not planned" --comment "…".
### Commencer le travail sur une issue
1. gh issue develop <n> --checkout --name fix/<n>-sujet-court : une branche liée à l'issue. La suite avec github-pr.

## Bonnes pratiques
- Une issue, un problème.
- Le titre dit le symptôme, pas la solution supposée.
- La sortie d'erreur exacte, nettoyée de tout secret et de toute donnée personnelle.

## Format de la réponse
En liste : numéro, titre, étiquettes, état et une ligne de résumé. Pour une création ou un tri : le texte ou les changements proposés, avant de les appliquer.

## Règles
### À faire toujours
- Présenter le texte d'une issue ou d'un commentaire avant de le publier.
- Citer le numéro et le lien de chaque issue touchée.
### À ne jamais faire
- Fermer, étiqueter, assigner ou commenter sans l'accord de l'utilisateur.
- Publier un secret, un journal non nettoyé ou des données personnelles dans une issue.
- Inventer une étiquette ou un responsable qui n'existe pas.

## Vérifier avant de répondre
- [ ] Doublon cherché avant toute création.
- [ ] Chaque changement validé par l'utilisateur avant d'être appliqué.
- [ ] Aucune donnée sensible dans le texte publié.

## Exemple
> Demande : « Trie les issues ouvertes du dépôt »
> Réponse attendue (résumé) : la liste avec le type proposé pour chacune, les doublons repérés, puis la demande de validation avant le moindre gh issue edit ou gh issue close.
