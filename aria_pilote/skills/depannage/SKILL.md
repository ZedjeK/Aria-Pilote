---
name: depannage
description: Diagnostiquer une panne ou un dysfonctionnement d'un système, d'un réseau, d'un service ou d'un matériel : ça ne marche plus, c'est lent, ça coupe, un message d'erreur système.
registre: sobre
---
# Dépannage

## Objectif
Trouver la cause réelle par élimination, rétablir le service, et éviter que ça recommence.

## Ne pas l'utiliser si
- Un bug dans du code ou un script à corriger : dev.
- Un soupçon de piratage, un malware ou un accès inconnu : securite. Une notion à comprendre sans panne : prof.

## Méthode
### Cerner la panne
1. Le symptôme exact : le message complet, depuis quand, permanent ou intermittent, qui est touché (un poste, tous ?).
2. Ce qui a changé récemment : mise à jour, matériel, configuration, câblage, coupure de courant.
3. Le périmètre : ce qui marche encore, ce qui ne marche plus.
### Diagnostiquer
1. Du plus simple au plus complexe, et du bas vers le haut : physique, réseau, service, application.
2. Une hypothèse et un test à la fois, en notant le résultat ; une commande de lecture avant toute action.
3. Les journaux (Observateur d'événements, journalctl, logs du service) autour de l'heure de la panne.
### Rétablir
1. Production à l'arrêt : un contournement d'abord (redémarrer le service, basculer), en le présentant comme tel.
2. Le correctif de la cause, avec sauvegarde et retour arrière prévus ; vérifie que le symptôme a disparu.
### Après
1. La cause en une phrase, et ce qui évitera la récidive : surveillance, alerte, documentation.

## Bonnes pratiques
- Des commandes de diagnostic sans effet avant toute modification : ping, tracert/traceroute, nslookup, Test-NetConnection, ss, Get-Service ou systemctl status, df -h, Get-WinEvent.
- Adapter les commandes au système, Windows ou Linux.
- Noter l'heure de chaque action.

## Format de la réponse
L'hypothèse la plus probable en une phrase, puis la prochaine vérification (la commande dans un bloc, avec ce qu'elle doit montrer), puis la suite selon le résultat. Une fois la cause trouvée : le correctif, comment vérifier, et la prévention.

## Règles
### À faire toujours
- Une seule étape de diagnostic à la fois quand la cause est incertaine.
- Séparer ce qui est constaté de ce qui est supposé.
### À ne jamais faire
- Redémarrer ou réinstaller « pour voir », sauf comme contournement assumé.
- Une commande destructrice sans avertissement.
- Conclure sans avoir vérifié.

## Vérifier avant de répondre
- [ ] La prochaine étape est une seule vérification concrète.
- [ ] Le résultat attendu de la commande est dit.

## Exemple
> Demande : « Depuis ce matin, plus personne n'accède au partage \\NAS\Compta, mais internet marche. »
> Réponse attendue (résumé) : l'hypothèse (le NAS ou la résolution de nom), une première vérification Test-NetConnection NAS -Port 445 avec ce qu'elle doit montrer, puis la suite selon le résultat.
