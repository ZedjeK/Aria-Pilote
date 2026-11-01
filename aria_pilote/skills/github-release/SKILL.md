---
name: github-release
description: Préparer et publier une version sur GitHub avec gh : numéro, tag, notes tirées des commits, release.
registre: professionnel
---
# Release GitHub

## Objectif
Une version publiée sans surprise : le bon numéro, un tag sur le bon commit, et des notes qui disent ce qui change pour l'utilisateur.

## Ne pas l'utiliser si
- Ouvrir ou fusionner une pull request : github-pr.
- Un simple message de commit : message-commit.

## Méthode
### Préparer
1. Vérifie l'état : git status --short (rien en cours), git switch <branche par défaut>, git pull, puis les tests du projet. Un test rouge : arrête-toi, signale-le et demande comment continuer ; une release ne corrige rien et n'ouvre aucune pull request d'elle-même.
2. La dernière version : gh release list --limit 5, ou git describe --tags --abbrev=0.
3. Les changements depuis : git log <dernier tag>..HEAD --oneline, et les pull requests fusionnées (gh pr list --state merged --limit 30).
4. Propose le numéro selon SemVer (correctif x.y.Z, nouveauté compatible x.Y.0, rupture X.0.0), ou selon la convention du projet s'il en a une.
### Rédiger les notes
1. Groupe par ce que l'utilisateur gagne : nouveautés, corrections, changements cassants avec la marche à suivre.
2. Écris-les dans .git/RELEASE_NOTES.md (write_file) et présente-les pour validation.
### Publier
1. Seulement après le feu vert. Si le projet a un numéro de version dans le code, commit « Version x.y.z » ; sur une branche par défaut protégée, ce commit passe par une pull request (github-pr), et le tag se pose après la fusion, sur la branche à jour.
2. Tag annoté, puis push du tag seul : git tag -a vX.Y.Z -m "Version X.Y.Z", git push origin vX.Y.Z.
3. gh release create vX.Y.Z --verify-tag --title "…" --notes-file .git/RELEASE_NOTES.md ; --prerelease pour une préversion, --draft pour relire d'abord sur GitHub.
4. Vérifie avec gh release view vX.Y.Z et donne le lien.

## Bonnes pratiques
- Des notes écrites pour l'utilisateur, pas la liste brute des commits.
- --generate-notes donne un premier jet, à relire et à réécrire.
- Une préversion (--prerelease) pour ce qui n'est pas encore sûr.

## Format de la réponse
Le numéro proposé et pourquoi, les notes à valider, puis, une fois publiée, le lien de la release.

## Règles
### À faire toujours
- Faire valider le numéro et les notes avant de taguer ou de publier.
- Taguer le commit qui a passé les tests.
### À ne jamais faire
- Publier une release sans que l'utilisateur l'ait demandé.
- Déplacer ou supprimer un tag déjà poussé.
- Annoncer dans les notes un changement absent du code.

## Vérifier avant de répondre
- [ ] Le tag pointe sur le bon commit, et la branche était à jour.
- [ ] Les notes couvrent tous les changements visibles, cassants compris.
- [ ] Le lien de la release est donné.

## Exemple
> Demande : « Prépare la release »
> Réponse attendue (résumé) : dernière version v1.4.2, six commits dont deux corrections et une nouveauté, proposition v1.5.0 et notes à valider ; après le feu vert, tag, gh release create et le lien.
