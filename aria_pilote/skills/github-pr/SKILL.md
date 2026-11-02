---
name: github-pr
description: Mener un changement sur GitHub de bout en bout avec gh : branche, commits, push, pull request, CI, revues et fusion.
registre: professionnel
---
# Pull request GitHub

## Objectif
Un changement livré sur sa branche, dans une pull request claire, CI au vert et revues traitées ; la fusion reste la décision de l'utilisateur.

## Ne pas l'utiliser si
- Seulement un message de commit : message-commit. Relire du code sans pull request : revue-code.
- Des issues : github-issues. Publier une version : github-release.

## Méthode
### Préparer
1. gh auth status et git status --short. gh absent ou non connecté : l'utilisateur lance lui-même winget install GitHub.cli ou gh auth login.
2. Sur la branche par défaut (gh repo view --json defaultBranchRef) : crée la branche de travail avant de toucher un fichier, git switch -c feat/sujet-court (fix/… pour un correctif).
### Commiter et pousser
1. Juste avant git commit, revérifie git branch --show-current. Un commit par sujet, message au format de message-commit, présenté avant.
2. Les tests du projet s'il en a, puis git push -u origin HEAD.
### Ouvrir la pull request
1. Le corps dans .git/PR_BODY.md (write_file, hors du suivi) : quoi, pourquoi, comment vérifier, « Closes #12 » si une issue est liée.
2. gh pr create --base <branche par défaut> --title "…" --body-file .git/PR_BODY.md (--draft si pas prêt) : sans titre ni corps, gh attend une saisie et bloque. Donne le lien.
### Suivre la CI
1. gh pr checks <n> ; pour attendre la fin, gh pr checks <n> --watch en arrière-plan (background), lu avec command_output.
2. Check rouge : gh run list --branch <branche>, gh run view <id> --log-failed, puis corrige la cause, commit, push.
3. CI au vert : arrête-toi et donne son état. Ce n'est pas une demande de fusion.
### Traiter les revues
1. gh pr view <n> --comments, et sur le code : gh api "repos/{owner}/{repo}/pulls/<n>/comments".
2. Corrige par de nouveaux commits, pousse, et propose une réponse (gh pr comment <n> --body "…").
### Fusionner, sur demande seulement
1. CI au vert, revues approuvées : gh pr merge <n> --squash, --merge ou --rebase selon le projet (sans option, gh bloque), --delete-branch s'il le veut.
2. git switch <branche par défaut>, puis git pull.

## Format de la réponse
Ce qui a été fait (branche, commits, lien), l'état de la CI tel quel, puis ce qui attend l'utilisateur.

## Règles
### À faire toujours
- Annoncer chaque action qui sort du PC (push, pull request, commentaire, fusion) avant de la lancer.
### À ne jamais faire
- Commiter sur la branche par défaut.
- Fusionner, fermer ou commenter sans demande explicite.
- Réécrire un historique poussé (push forcé, rebase, amend) : le proposer.
- Contourner un garde-fou : --admin, --no-verify, check désactivé.
- Un secret dans un commit, une pull request ou un commentaire.

## Vérifier avant de répondre
- [ ] Aucun commit sur la branche par défaut, chaque action sortante annoncée.
- [ ] Le lien est donné, l'état de la CI dit sans l'embellir.

## Exemple
> Demande : « Ouvre une PR pour ma correction du délai de téléchargement »
> Réponse attendue (résumé) : branche fix/delai-telechargement, commit présenté puis validé, tests, push, gh pr create avec un corps, puis le lien et l'état de la CI.
