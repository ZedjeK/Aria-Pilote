---
name: traduction
description: Traduire un texte d'une langue à une autre (mail, documentation, message d'erreur, interface, article), ou relire une traduction.
registre: professionnel
---
# Traduction

## Objectif
Une traduction fidèle et naturelle, dans le bon registre, qui garde la terminologie technique.

## Ne pas l'utiliser si
- Écrire directement un texte dans une autre langue, sans texte source : redaction.
- Résumer un texte en langue étrangère : resume.

## Méthode
### Traduire
1. Repère la langue cible (le français par défaut) et le destinataire ; le registre suit l'original, sauf demande contraire.
2. Traduis le sens, pas le mot à mot ; garde la structure (titres, listes, blocs de code).
3. Ne traduis ni le code, ni les commandes, ni les noms de produits, chemins et identifiants ; les termes techniques gardent leur forme usuelle dans le domaine.
4. Tiens un glossaire constant : un terme source, une seule traduction dans tout le texte.
### Relire une traduction
1. Compare phrase à phrase avec l'original : contresens, oublis, ajouts, faux amis.
2. Corrige en gardant le style du traducteur, et liste les corrections importantes.

## Format de la réponse
La traduction d'abord, prête à copier, sans préambule ; puis, si utile, une courte liste des choix de terminologie et des passages ambigus.

## Règles
### À faire toujours
- Signaler un passage ambigu ou intraduisible au lieu de deviner.
- Respecter les conventions de la langue cible (guillemets, espaces, dates, nombres).
### À ne jamais faire
- Ajouter ou retirer une information.
- Traduire un bloc de code ou une commande.
- Laisser un faux ami (eventually n'est pas « éventuellement », actually n'est pas « actuellement »).

## Vérifier avant de répondre
- [ ] Rien d'ajouté, rien d'omis.
- [ ] Le code et les noms propres sont intacts.
- [ ] Chaque terme est traduit de la même façon partout.

## Exemple
> Demande : « Traduis ce mail en anglais pour le support Synology : le volume 1 est passé en mode dégradé après le remplacement du disque 2. »
> Réponse attendue (résumé) : le mail en anglais (« Volume 1 switched to degraded mode after replacing drive 2… ») avec une formule de politesse adaptée, puis une note : « mode dégradé » est rendu par « degraded », le terme de DSM.
