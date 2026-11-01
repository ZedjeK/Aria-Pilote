---
name: github-pr
description: Mener un changement sur GitHub de bout en bout avec gh : branche, commits, push, pull request, CI, revues et fusion.
registre: professionnel
---
# Pull request GitHub

## Objectif
Un changement livré proprement : sur sa branche, dans une pull request claire, CI au vert et revues traitées, fusionné seulement quand l'utilisateur le décide.

## Ne pas l'utiliser si
- Rédiger seulement un message de commit : message-commit.
- Créer, trier ou commenter des issues : github-issues. Publier une version : github-release.
- Relire du code sans pull request : revue-code.

## Méthode
### Préparer
1. Vérifie l'outillage : gh auth status, git status --short, git branch --show-current. gh absent ou non connecté : demande à l'utilisateur de lancer lui-même winget install GitHub.cli ou gh auth login.
2. Repère le dépôt et sa branche par défaut : gh repo view --json nameWithOwner,defaultBranchRef.
3. Sur la branche par défaut : crée la branche de travail tout de suite, avant de modifier le moindre fichier (git switch -c feat/sujet-court, fix/… pour un correctif). Aria refuse le push direct sur une branche protégée (main et master par défaut), mais un commit fait dessus reste : la branche locale diverge alors de GitHub.
### Commiter et pousser
1. Juste avant git commit, vérifie git branch --show-current : jamais la branche par défaut.
2. Un commit par sujet, avec un message au format de message-commit, présenté avant git commit.
3. Lance les tests du projet, s'il en a, avant de pousser.
4. Pousse la branche : git push -u origin HEAD.
### Ouvrir la pull request
1. Écris le corps dans .git/PR_BODY.md (write_file, hors du suivi de git) : quoi, pourquoi, comment vérifier, et l'issue liée (« Closes #12 »).
2. gh pr create --base <branche par défaut> --title "…" --body-file .git/PR_BODY.md, avec --draft si le travail n'est pas prêt à relire. Toujours un titre et un corps : sans eux, gh attend une saisie et la commande reste bloquée.
3. Donne le lien que renvoie gh.
### Suivre la CI
1. gh pr checks <n> donne l'état des vérifications. Pour attendre la fin, lance gh pr checks <n> --watch en arrière-plan (background) et lis-le avec command_output : au premier plan, il dépasserait le délai.
2. Un check rouge : gh run list --branch <branche>, puis gh run view <id> --log-failed. Corrige la cause, commit, push.
### Traiter les revues
1. Lis la discussion et les avis : gh pr view <n> --comments ; les commentaires sur le code : gh api "repos/{owner}/{repo}/pulls/<n>/comments" (entre guillemets pour PowerShell).
2. Corrige par de nouveaux commits, pousse, puis propose une réponse (gh pr comment <n> --body "…") qui dit ce qui a changé.
### Fusionner
1. Seulement à la demande de l'utilisateur, CI au vert et revues demandées approuvées.
2. gh pr merge <n> avec --squash, --merge ou --rebase selon la convention du projet (sans option, gh attend une saisie) ; --delete-branch si l'utilisateur veut supprimer la branche.
3. Remets le dépôt local à jour : git switch <branche par défaut>, puis git pull.

## Bonnes pratiques
- Des pull requests petites et centrées sur un seul sujet : plus faciles à relire.
- Une CI rouge se corrige à la source : lis le log avant de toucher au code.
- Le brouillon (--draft) tant que ce n'est pas prêt, l'issue liée dans le corps pour qu'elle se ferme à la fusion.

## Format de la réponse
Ce qui a été fait (branche, commits, pull request avec son lien), l'état de la CI tel quel (vert, rouge avec sa cause, en cours), puis ce qui attend l'utilisateur (relire, valider la fusion…).

## Règles
### À faire toujours
- Une branche de travail par sujet, jamais de commit direct sur la branche par défaut.
- Annoncer chaque action qui sort du PC (push, pull request, commentaire, fusion) avant de la lancer.
### À ne jamais faire
- Fusionner, fermer une pull request ou commenter sans demande explicite de l'utilisateur.
- Réécrire un historique déjà poussé (push forcé, rebase, amend) : le proposer à l'utilisateur.
- Contourner un garde-fou : --admin, --no-verify, check désactivé.
- Mettre un secret dans un commit, un corps de pull request ou un commentaire.

## Vérifier avant de répondre
- [ ] La branche de travail existait avant le premier commit : aucun commit sur la branche par défaut.
- [ ] Tests lancés, chaque action sortante annoncée.
- [ ] La pull request a un titre clair et un corps : quoi, pourquoi, comment vérifier.
- [ ] Le lien est donné, et l'état de la CI dit sans l'embellir.

## Exemple
> Demande : « Ouvre une PR pour ma correction du délai de téléchargement »
> Réponse attendue (résumé) : branche fix/delai-telechargement, commit présenté puis validé, tests au vert, push, gh pr create avec un corps (quoi, pourquoi, comment vérifier), puis le lien de la PR et l'état de la CI.
