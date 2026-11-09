---
name: projet
description: Organiser, planifier ou prioriser un projet, une échéance, « par où commencer » ; concevoir une infra ou un réseau.
registre: sobre
---
# Projet

## Objectif
Un plan réaliste qui finit par une action concrète, faisable aujourd'hui.

## Ne pas l'utiliser si
- Une tâche simple à faire tout de suite : agis directement.
- Choisir entre plusieurs solutions : analyste. Rédiger le compte rendu ou le mail : redaction.

## Méthode
### Cadrer
1. Objectif SMART (spécifique, mesurable, atteignable, réaliste, daté) ; s'il manque, demande-le avant tout découpage.
2. Besoins, périmètre inclus et exclu, contraintes (budget, délai, matériel existant), critères de fin vérifiables ; à plusieurs, qui fait, qui décide.
### Concevoir une infra ou un réseau (seulement dans ce cas)
1. Inventaire de l'existant, schéma logique (zones, services, flux) puis physique.
2. Plan d'adressage et VLAN par usage (administration, serveurs, utilisateurs, invités), avec marge de croissance.
3. Sécurité dès la conception : cloisonnement, flux autorisés au strict nécessaire, administration sur un réseau dédié.
4. Points uniques de défaillance à doubler selon l'impact et le budget ; sauvegarde 3-2-1 avec test de restauration ; supervision et alertes.
### Planifier
1. Lots, puis tâches finissables en une session ; priorité MoSCoW (indispensable, important, souhaitable, pas cette fois).
2. Dépendances, jalons, chemin critique ; estimation en trois points : une fourchette avec son hypothèse.
3. Risques : « à l'échéance, le projet a échoué, pourquoi ? » ; pour chacun probabilité, impact, parade, signal d'alerte ; le probable et grave d'abord.
### Exécuter et clôturer
1. Une étape à la fois, tableau à faire / en cours / fait ; à chaque reprise : fait, bloqué, écart, risques. Tout ajout de périmètre : mesure son impact sur le délai et le coût avant de l'accepter.
2. Migration : état initial sauvegardé, bascule par étapes avec test et retour arrière, fenêtre annoncée, l'ancien système garde la main jusqu'à validation du nouveau.
3. Clôture : critères de fin vérifiés un par un, dossier d'architecture à jour, bilan, archivage.

## Format de la réponse
Objectif · Périmètre · Terminé quand · Tâches par priorité · Jalons et chemin critique · Estimation (fourchette) · Risques (risque, probabilité × impact, parade, signal) · Migration (étapes, test, retour arrière) · Prochaine action : une seule.
Version courte pour un petit projet : on ne remplit pas une case pour le principe.

## Règles
### À faire toujours
- Finir par une prochaine action concrète.
### À ne jamais faire
- Une durée inventée : une fourchette avec son hypothèse, ou rien.
- Dérouler plus d'une étape à la fois quand on exécute.
- Un risque sans parade.
- Accepter un ajout au périmètre sans en mesurer l'impact.
- Une bascule sans retour arrière prévu.

## Vérifier avant de répondre
- [ ] L'objectif est mesurable et daté, les critères de fin sont vérifiables.
- [ ] Chaque risque a sa parade, et le plan se termine par une seule action faisable aujourd'hui.

## Exemple
> Demande : « Je veux migrer mon petit serveur vers un nouveau matériel, par où commencer ? »
> Réponse attendue (résumé) : un objectif daté avec ses critères de fin, des étapes avec retour arrière, les risques et leurs parades, puis une seule prochaine action : sauvegarder l'existant.
