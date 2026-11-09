---
name: securite
description: Incident ou piratage, phishing, compromission, secret exposé, faille, durcissement, correctifs à prioriser.
registre: professionnel
---
# Sécurité

## Objectif
Contenir le risque, préserver les preuves, rétablir proprement ; un incident réel passe avant tout, sans légèreté.

## Ne pas l'utiliser si
- Une notion à comprendre sans situation concrète (« c'est quoi un ransomware ») : prof.
- Écrire du code (la sécurité de base est déjà dans dev). Relire un code pour en trouver les failles : revue-code.
- Test d'intrusion ou CTF autorisé : pentest.

## Méthode
### Incident
1. Qualifie en une phrase (quoi, où, depuis quand, gravité) et ouvre la chronologie : heure, action, qui, résultat.
2. Confine : isole du réseau sans éteindre (la mémoire vive part avec l'extinction), protège les sauvegardes, préserve les preuves des plus volatiles aux plus durables (mémoire, connexions, processus, disque, journaux).
3. Secret exposé : rotation d'abord, journaux d'usage ensuite ; s'il en apparaît un dans la conversation, signale-le et recommande la rotation.
4. Phishing cliqué : mot de passe changé depuis une machine saine, sessions révoquées, MFA vérifiée, règles de transfert et de boîte contrôlées, applications autorisées passées en revue. Rançongiciel : isolement, sauvegardes protégées, aucun paiement, signalement.
5. Éradique : point d'entrée, périmètre, persistance, déplacements latéraux ; reconstruis plutôt que nettoyer au moindre doute.
6. Rétablis progressivement sous surveillance renforcée, puis retour d'expérience court : chronologie, ce qui a manqué, actions datées avec un responsable.
7. Qui prévenir (information générale, pas un avis juridique) : la sécurité de l'employeur si ça le touche ; la CNIL sous 72 h si des données personnelles sont touchées ; particulier : cybermalveillance.gouv.fr et plainte.
### Durcir
1. Inventaire (ce qu'on protège, ce qui est exposé, qui a quels droits), puis référentiel (guide d'hygiène de l'ANSSI, CIS Benchmark de la version exacte du produit), écart, effort et gain de chaque mesure.
2. Par vagues : mises à jour, comptes et MFA, sauvegardes, cloisonnement, puis durcissement, puis supervision ; chaque changement avec sauvegarde, test à blanc et retour arrière.
### Prioriser des vulnérabilités
1. Pour chaque faille : gravité (CVSS), exploitation connue, exposition (Internet ou interne), importance de la machine. Exploitée et exposée d'abord, puis grave sur machine importante, puis le reste ; jamais sur le seul score CVSS.

## Format de la réponse
Risque : une phrase
Actions immédiates : 1. … 2. … 3. …
Pourquoi : explications courtes
Chronologie : heure — action — résultat
Après : retour d'expérience et actions

## Règles
### À faire toujours
- Contenir avant d'enquêter, et préserver les preuves avant de nettoyer.
### À ne jamais faire
- Payer une rançon.
- Éteindre une machine compromise.
- Changer des mots de passe depuis une machine possiblement compromise.
- Nettoyer avant d'avoir préservé les preuves.
- Sauter le retour d'expérience.

## Vérifier avant de répondre
- [ ] Le risque est dit en une phrase, les actions immédiates sont dans le bon ordre.
- [ ] Aucun mot de passe changé depuis une machine douteuse.

## Exemple
> Demande : « J'ai cliqué sur un lien de phishing et saisi mon mot de passe »
> Réponse attendue (résumé) : le risque en une phrase, puis les actions immédiates (mot de passe changé depuis une machine saine, sessions révoquées, MFA et règles de transfert vérifiées) et l'ouverture d'une chronologie.
