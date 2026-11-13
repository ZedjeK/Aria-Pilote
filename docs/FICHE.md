# Fiche projet : Aria Pilote

Relevé du code au 13/11/2026, version 0.26.0, puis mis à jour avec le navigateur piloté. Une ligne « à renseigner » est une décision qui reste à prendre.

## Identité

| Élément | Valeur |
| --- | --- |
| Nom | Aria Pilote |
| Promesse | Tes modèles d'IA en local, et un agent qui travaille dans tes dossiers sous ton contrôle. |
| Élément orange du logo | Une boussole |
| Fond animé | À renseigner : l'interface n'en a pas encore (motif, ce qui est tiré au hasard, nombre par défaut) |
| Stack | Python 3.11 (FastAPI, uvicorn), interface HTML et JavaScript sans framework, lanceur en C# ; moteurs llama.cpp |
| Version | `aria_pilote/__init__.py` (`VERSION`) |
| Critère de réussite de la v0.1 | Sans objet : l'appli est en 0.26 |

## Comportement

| Élément | Valeur |
| --- | --- |
| Droits administrateur | Aucun |
| Démarrage automatique | Aucun |
| Port local | 8700 (interface), 8701 (llama-server), 8888 (SearXNG) ; version de dev : 8710, 8711, 8898. Écoute sur `127.0.0.1` seulement |
| Dossier de données | `%USERPROFILE%\.Aria-Pilote` (le code de l'appli s'y trouve aussi) |

## Données

| Élément | Où |
| --- | --- |
| Réglages | `config.json`, `mcp.json` |
| Conversations, presets, journaux, sauvegardes de l'agent, souvenirs | `data\` |
| Modèles | `models\` |
| Moteurs et SearXNG | `engines\`, `services\` |
| Chromium du navigateur piloté | `chromium\` : fait partie du code, remplacé à chaque mise à jour (il doit aller avec la version de Playwright) |
| Profil du navigateur piloté | `data
avigateur\profil\` : à part, sans les cookies, mots de passe ni historique de l'utilisateur ; « vider » l'envoie à la corbeille |
| Compétences et sous-agents personnels | `skills\`, `agents\` |
| Clé de llama-server | `data\llama.key` : tirée au hasard à chaque chargement, effacée à l'arrêt, en clair le temps de la session |
| Clé secrète de SearXNG | `services\searxng\settings.yml` : tirée au hasard au premier lancement, en clair |
| Jetons et variables des serveurs MCP | `mcp.json`, en clair |
| Jeton GitHub | Jamais lu ni stocké par Aria : il reste dans le coffre de Windows, géré par gh |

## Écritures hors du dossier de données

| Écriture | Accord |
| --- | --- |
| Raccourcis « Aria Pilote » sur le bureau et dans le menu Démarrer, à l'installation | Non demandé |
| Fichiers du dossier de travail attaché à une conversation (le travail de l'agent) | Accord à chaque modification, sauvegarde avant |
| Corbeille de Windows (modèles, sauvegardes et conversations supprimés) | Action de l'utilisateur |
| Scripts temporaires `aria-commande-*.ps1` et `aria-hook-*.ps1` dans `%TEMP%`, effacés après usage | Non demandé |
| Commandes lancées par l'agent ou tapées dans le terminal : peuvent écrire n'importe où | Accord à chaque commande |
| Contrôle du PC (expérimental, coupé par défaut) : agit dans d'autres applications | Accord par action, sauf actions ordinaires autorisées pour la tâche |

## Connexions sortantes

| Destination | Pourquoi | Quand |
| --- | --- | --- |
| `api.github.com` | Dernière version d'Aria Pilote : un GET anonyme des tags du dépôt | Au démarrage, au plus une fois par 24 h, si la recherche de mises à jour est activée (elle l'est par défaut) ; et sur demande |
| `api.github.com` | Dernière build de chaque moteur | Sur demande seulement |
| `github.com` et ses serveurs de fichiers | Téléchargement des moteurs | Sur demande |
| `huggingface.co` et ses serveurs de fichiers | Recherche et téléchargement de modèles | Sur demande |
| Moteurs de recherche configurés dans SearXNG | Recherche web de l'agent | Avec accord, si le web est activé |
| Toute page publique | `web_fetch` de l'agent | Avec accord ; adresses locales et du réseau local refusées |
| Toute page publique, et ce qu'elle charge | `naviguer` de l'agent, dans le Chromium embarqué | Avec accord ; chaque requête de la page (redirections, images, scripts, WebSocket) vers ta machine ou ton réseau local est bloquée |
| Serveurs MCP distants | Outils ajoutés par l'utilisateur | Selon leur réglage (accord par défaut) |
| GitHub par git et gh | Travail de l'agent sur un dépôt | Accord à chaque commande qui sort du PC |
| `github.com/astral-sh/python-build-standalone`, `codeload.github.com` | Construction de l'exe seulement (Python portable, SearXNG) | Jamais par l'appli installée |
| `cdn.playwright.dev` (et ses miroirs `playwright.download.prss.microsoft.com`) | Construction de l'exe et version de dev : Chromium du navigateur piloté | Jamais par l'appli installée |

## Composants tiers embarqués

| Composant | Licence |
| --- | --- |
| llama.cpp (officiel) et le fork de PrismML | MIT |
| SearXNG, au commit épinglé dans `tools/install_searxng.py` | AGPL-3.0 |
| python-build-standalone | Voir sa licence (plusieurs composants) |
| xterm.js et son module fit | MIT |
| Playwright (paquet Python et son pilote Node.js) | Apache-2.0 |
| Chromium (build « Chrome for Testing » fournie par Playwright) | BSD-3-Clause, et les licences de ses composants (fichier `ABOUT` livré avec lui) |
| FFmpeg et winldd, téléchargés par Playwright avec Chromium | FFmpeg : LGPL-2.1 (`COPYING.LGPLv2.1` livré avec lui) ; winldd : outil de Playwright |
| Space Grotesk, IBM Plex Mono | SIL OFL 1.1 |
| Paquets Python de `requirements.txt` | Chacun la sienne |

## Écarts au contrat des applis Aria

Relevés en écrivant cette fiche ; chacun se traite par une PR à part, le contrat d'abord.

| Écart | Règle du contrat |
| --- | --- |
| Raccourcis créés sans demander | Aucun raccourci créé sans accord |
| Jetons des serveurs MCP en clair dans `mcp.json` | Aucun secret en clair : au minimum DPAPI |
| Scripts temporaires dans `%TEMP%` | Rien dans `%TEMP%` |
| Pas de fond animé | Fond animé obligatoire, avec ses réglages |
| Pas d'icône de notification ni d'instance unique | Icône de notification pour une appli en fond ; relancer ramène la fenêtre existante |
| Pas de `build.ps1`, `tools/tester.ps1`, `tools/version.ps1` | Interface commune des dépôts |
| Job de CI nommé `unittest` | Le modèle l'appelle `tests` ; ici, le ruleset exige `unittest` : ne pas le renommer sans changer le ruleset dans la même opération |
| Pas de `CHANGELOG.md`, `LICENCES-TIERS.txt`, `docs/releases/` | Fichiers à tenir à jour |
| Mise à jour : l'appli lit les tags et renvoie vers la release, sans télécharger ni vérifier d'empreinte | Proposer, télécharger depuis la release, vérifier le SHA-256, garder l'ancienne version |
