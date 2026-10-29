<p align="center">
  <img src="docs/logo.png" width="160" alt="Logo d'Aria Pilote : un A néon glitché avec une boussole orange">
</p>

<h1 align="center">Aria Pilote</h1>

<p align="center">
  Tes modèles d'IA en local, et un agent qui travaille dans tes dossiers sous ton contrôle.<br>
  Tout se passe sur ton PC : les modèles tournent sur ta carte graphique, rien ne part dans le cloud.<br>
  Rien à installer : un dossier portable et un exe.
</p>

<p align="center">
  <a href="#compiler-soi-même"><b>Compiler Aria Pilote</b></a> · v0.20 · Windows 10/11 x64 · carte NVIDIA conseillée · licence MIT (code d'Aria) / AGPL-3.0 (SearXNG embarqué)
</p>

## Ce qu'il fait

| Fonction | Ce que ça fait | Moteur |
| --- | --- | --- |
| **Réglages** | Une vraie page, avec la navigation à gauche comme dans Claude Code ou LM Studio : Général (recherche de mises à jour sur GitHub, notifications Windows : tâche terminée, agent qui t'attend, modèle chargé, téléchargement fini), Modèles (modèle par défaut et chargement au démarrage, Mes modèles, Presets, Découvrir, Moteurs, Matériel et optimisation) Agents (Comportement : réglages de départ des nouvelles conversations et limite d'appels d'outils ; Autorisations de tous tes projets ; Compétences et sous-agents ; Hooks ; Serveurs MCP ; Web et SearXNG : recherche désactivable, nombre de résultats, langue, SafeSearch, démarrage, arrêt après inactivité) et Système (Données : conversations, sauvegardes, journaux, corbeille automatique). Chaque réglage s'enregistre tout seul | |
| **Modèles locaux** | Trouve tes fichiers GGUF et lit leur en-tête. Dans **Réglages › Mes modèles**, la fiche d'un modèle montre **le fichier** en clair (architecture, couches, têtes d'attention, attention hybride, experts MoE, couche de prédiction multi-tokens, projecteur vision, tirage conseillé par l'auteur…) et **tous ses réglages de chargement**, chacun avec ce qu'il change : moteur, couches sur la carte graphique, experts MoE gardés en RAM, mode de chargement (mmap, verrouillé en RAM), threads, contexte, cache K et cache V séparés (normal, q8_0, q4_0), flash attention, batch et micro-batch, prédiction multi-tokens (MTP : mesurée de 66 à environ 120 tok/s sur Bonsai MTP avec PrismML, instable avec llama.cpp officiel), vision. **💾 Enregistrer** (actif dès qu'un réglage change), **Enregistrer et charger**, **▶ Charger** sans enregistrer, et **↺ Revenir aux réglages par défaut**. En tête de liste, **⚙ Réglages par défaut** vaut pour tous les modèles qui n'ont pas les leurs | llama.cpp |
| **Optimiser mes modèles** | Dans Réglages › Matériel et optimisation : scan du PC (carte graphique et VRAM, processeur, RAM, avec leur occupation), puis trois choix calculés pour chaque modèle d'après son en-tête GGUF, avec leurs conséquences — **le plus rapide** (16k, cache normal, VRAM laissée libre), **le compromis** (jusqu'à 32k, conseillé) et **le plus de contexte** (autant que la VRAM le permet). Les applications qui occupent la carte graphique sont signalées (elles faussent le calcul). **L'essai réel** charge chaque modèle avec les réglages choisis, mesure la VRAM et la vitesse, descend d'un palier si ça ne tient pas (au moins 0,5 Go laissés libres), puis remet ton modèle comme avant. Rien n'est mémorisé avant « Appliquer » | — |
| **Bonsai 2 et formats ternaires** | Les fichiers PQ2_0 et PTQ1_0 de PrismML partent tout seuls sur le bon moteur. Si le moteur officiel refuse un fichier, Aria bascule et retient le choix | Fork PrismML de llama.cpp |
| **Découvrir** | Recherche sur Hugging Face, fichiers d'un dépôt avec leur taille et une estimation « tient sur le GPU / GPU + RAM / trop gros ». Téléchargement avec reprise, vérification SHA-256 et rangement automatique. Un modèle supprimé part à la corbeille | Hugging Face |
| **Chat** | Réponses en streaming, presets (prompt système, échantillonnage, réflexion activée ou non : la puce 🎛 près de la saisie, gérés dans Réglages › Presets avec une explication sous chaque réglage), réflexion du modèle repliable, Markdown avec blocs de code copiables, historique des conversations. Quand la conversation s'allonge, Aria résume toute seule les anciens échanges avant le prochain message ; la jauge de contexte montre ce seuil, une alerte prévient à son approche, et le bouton 🗜 (ou `/compact`, avec une consigne facultative : « /compact garde les décisions ») compacte à la demande, sans rien effacer de l'historique affiché | llama-server (API compatible OpenAI) |
| **Agent** | Une seule conversation pour tout : c'est le modèle qui juge s'il discute ou s'il agit. Attacher un dossier lui ouvre les outils de fichiers ; sans dossier, il garde les compétences, le web (avec ton accord) et les sous-agents qui n'en ont pas besoin. Pour une tâche importante ou floue, il prépare d'abord un plan (sous-agent `planificateur`) et te le soumet : tant que tu ne l'as pas approuvé, Aria refuse toute modification et toute commande (option « Toujours proposer un plan » dans les Réglages ; pour une seule demande, la puce **🗺 Plan d'abord** près de la saisie, ou `/plan ta demande`). Dans le dossier que tu lui donnes : lister, lire, chercher, modifier des fichiers (plusieurs changements dans un même fichier en un seul accord, appliqués tout ou rien) et lancer des commandes PowerShell, y compris en arrière-plan (un serveur qu'il lance, interroge puis arrête ; arrêté de toute façon en fin de tâche, sauf si tu lui demandes de le laisser tourner). Chaque modification arrive avec son diff, chaque fichier est sauvegardé avant d'être touché, et « Annuler cette modif » le remet comme avant. « Annuler toute la tâche » remet d'un coup tous les fichiers de la tâche dans leur état d'avant (les versions actuelles partent à la corbeille, les fichiers retouchés depuis sont signalés et laissés tels quels, les commandes lancées sont rappelées car elles ne s'annulent pas) | Outils d'Aria |
| **Garde-fous** | Ton accord avant chaque modification, commande ou accès web. Pour une commande sûre que tu valides souvent, « Toujours autoriser dans ce projet » retient la commande exacte ou son début (`python -m unittest*`) ; une règle ne couvre jamais une suite avec `;` `&` `|` `>` `$` ou un retour à la ligne, elle est stockée côté Aria (jamais dans le projet) et le bouton **Règles** de la barre Agent ouvre **Réglages › Autorisations**, qui liste celles de tous tes projets et permet de les retirer. Dossier de travail cloisonné, liens et jonctions compris. Liste noire de commandes refusées d'office (formatage, registre système, désactivation de l'antivirus, télécharger-puis-exécuter…). 25 appels d'outils au plus par tâche (réglable de 10 à 100). Après chaque écriture, la syntaxe du fichier est vérifiée sans l'exécuter (Python, JSON, YAML, TOML, XML, PowerShell) et l'agent voit tout de suite la ligne fautive (désactivable dans Réglages › Comportement) | — |
| **Panneau latéral** | À droite du chat, comme dans Claude Code (bouton ▤) : l'onglet **Plan** suit la liste d'étapes de l'agent, l'onglet **Modifications** montre chaque fichier modifié, groupé par tâche, avec son diff réel (y compris après une annulation), sa syntaxe et les boutons « Annuler cette modif » et « Annuler toute la tâche ». L'onglet **⚙ Arrière-plan** fait défiler en direct la sortie des commandes de l'agent (serveurs, longs builds), avec un bouton Arrêter et les 10 dernières commandes terminées ; l'onglet **🧠 Contexte** montre l'occupation du contexte (résumés compris), le modèle et ses réglages, et tout ce que l'agent lit : ARIA.md, compétences, sous-agents, hooks et règles du projet. Replié, le bouton compte les modifications et les commandes en cours. Ouvert d'office en mode Agent ; sur un écran étroit, il passe par-dessus le chat | — |
| **Plan et mémoire** | L'agent écrit son plan d'étapes et le coche au fil du travail : il s'affiche en direct sous la conversation (✓ fait, roue en cours, cercle à venir), avec ou sans dossier, et se replie une fois terminé. Un fichier `MEMOIRE.md` à la racine d'un projet lui donne tes conventions à chaque tâche (l'ancien `ARIA.md` est encore lu, et l'onglet Contexte propose de le renommer) | — |
| **Persona et fiche Toi** | Qui te répond, sans écrire de prompt : un nom, un avatar, une présentation, des traits choisis dans un catalogue de 96, rangés par famille (tempérament, relation à toi, humour, façon de s'exprimer, habitudes de réponse, défauts attachants, séduction et intimité), avec leur intensité dans trois registres (naturel, sobre, professionnel), et des goûts détaillés (101 thèmes, chacun avec son intensité — aime bien, adore, passion — et une précision libre comme « Jeux vidéo : NieR, Horizon » ; coups de cœur, ce qu'elle n'aime pas, avis tranchés), des règles d'or et un interrupteur pour le contenu intime. Chaque compétence a son registre : `/revue-code` peut rester sobre quand la discussion est chaleureuse. Plusieurs personas, un par conversation (puce 🎭), export et import. La fiche **Toi** (prénom, tutoiement, langue, métier, ce que tu apprécies) vaut pour tous les personas. Tout reste dans tes données, jamais sur GitHub | — |
| **Compétences** | Des méthodes de travail que le modèle charge tout seul quand ta demande y correspond, sans déclencheur à écrire : dev, projet, securite, depannage, prof, redaction, resume, traduction, analyste, creatif, revue-code, script-powershell, message-commit, memoire-projet (le MEMOIRE.md d'un dossier) et createur-competence (qui t'aide à écrire les tiennes) (« /nom » en force une). Chacune suit le même modèle (objectif, quand ne pas s'en servir, méthode par cas, format, règles, vérifications, exemple) et se modifie avec un formulaire ou en mode texte ; une compétence intégrée se personnalise en copie. Sous la réponse, l'étiquette 🧩 dit laquelle a servi | Outils d'Aria |
| **Serveurs MCP** | Branche des serveurs MCP (Model Context Protocol) pour donner de nouveaux outils à l'agent : GitHub, bases de données, fichiers, navigateur… Dans **Réglages › Serveurs MCP**, ajoute un serveur local (une commande) ou distant (une adresse, avec ses en-têtes), ou colle directement une config `mcpServers` trouvée sur le web (même format que Claude Desktop et Claude Code). Chaque serveur affiche son état, ses outils et ce qu'ils coûtent en contexte ; chaque outil se règle sur *Demander* (par défaut), *Toujours autoriser* ou *Désactivé*. Un serveur est actif d'office ou à la demande, et la puce 🔌 du chat choisit ceux de la conversation. Chaque appel passe par ton accord et par le verrou du plan ; ce qu'un serveur renvoie est une donnée pour le modèle, jamais un ordre. Les sous-agents n'y ont pas accès | SDK officiel `mcp` |
| **Pièces jointes** | Colle (Ctrl+V), glisse ou joins (📎) jusqu'à 5 pièces par message. **Images** (une capture d'erreur, une photo, un schéma) : réduites, rangées avec la conversation et lues par le projecteur vision du modèle (mmproj) ; avec un modèle sans vision, Aria le dit et nomme ceux qui voient. **Textes, code et PDF** (`.txt`, `.md`, `.log`, `.csv`, `.json`, `.py`, `.ps1`… jusqu'à 10 Mo) : Aria en lit le texte et le donne au modèle, balisé comme une donnée (une consigne écrite dans un fichier ne s'exécute pas) ; au-delà de 50 000 caractères, le début seulement, et le modèle le sait. Un format non pris en charge est signalé | `pypdf` |
| **Contrôle du PC** (expérimental) | Coupé par défaut. Une fois activé dans les Réglages, « 🖱 Prendre la main » laisse l'agent voir les fenêtres que tu coches et y agir : il lit leurs boutons et champs par l'accessibilité de Windows, les numérote sur la capture, puis clique, tape ou fait défiler, avec ton accord à chaque action. Les menus (clic droit compris) et les boîtes de dialogue (Enregistrer sous, Ouvrir…) qu'une fenêtre ouvre font partie d'elle : il les voit, numérotés en tête, et peut y cliquer. Il rouvre lui-même une fenêtre réduite autorisée (avec ton accord) et zoome sur une zone de la capture pour lire un petit texte. Il n'agit que sur ce qu'il vient d'observer, refuse si la fenêtre a bougé ou si une autre appli la recouvre, ne lit ni ne remplit jamais un mot de passe, signale les boutons sensibles (envoyer, payer, supprimer…) et s'arrête au bout de 40 actions. Réglages système, sécurité Windows, terminaux et la fenêtre d'Aria restent hors de portée | Outils d'Aria |
| **Questions** | Quand une info indispensable lui manque, l'agent te pose la question avec des options au lieu de deviner. La tâche se met en pause, survit à un rechargement de la page, et reprend dès ta réponse (un bouton, ou un message tapé) | — |
| **Recherche web** | Recherche privée par un SearXNG qui tourne sur ton PC, lecture de pages publiques. Les adresses de ta machine et de ton réseau local sont refusées | SearXNG embarqué |
| **Sécurité locale** | llama-server n'accepte que les requêtes d'Aria (clé régénérée à chaque lancement, CORS limité), l'interface refuse les autres sites et le DNS rebinding, et tout ce qu'Aria lance s'arrête avec elle | — |

## Compiler soi-même

Pas encore de release téléchargeable : avec ses moteurs, le dossier dépasse la taille d'un fichier de release GitHub (2 Go). En attendant, la construction se fait en quatre commandes.

**Prérequis** : Windows 10/11 x64, [Python 3.11](https://www.python.org/downloads/) pour la construction (l'appli finale embarque son propre Python), et une carte NVIDIA avec un pilote récent. Sans carte NVIDIA, ajoute `--backend vulkan` ou `--backend cpu` à la première commande.

```powershell
git clone https://github.com/ZedjeK/Aria-Pilote.git "Source code"
cd "Source code"
py -3.11 tools/install_engines.py
py -3.11 tools/install_searxng.py
py -3.11 tools/build_portable.py
```

| Commande | Ce qu'elle fait |
| --- | --- |
| `install_engines.py` | Télécharge `llama-server` officiel (dernière build) et celui de PrismML (version épinglée) avec le runtime CUDA |
| `install_searxng.py` | Télécharge SearXNG à une version épinglée, lui applique la retouche Windows et génère sa configuration (clé secrète aléatoire, `127.0.0.1` uniquement) |
| `build_portable.py` | Assemble `..\Aria-Pilote-0.20.0\` : Python portable vérifié par SHA-256, code, moteurs, SearXNG et le lanceur `Aria-Pilote.exe` |

Lance ensuite `Aria-Pilote.exe`. L'exe n'est pas signé : si Windows SmartScreen affiche « Windows a protégé votre ordinateur », clique sur « Informations complémentaires » puis « Exécuter quand même ».

Pour développer sans construire : `run.ps1` lance l'appli depuis le code source.

La version de développement ne prend jamais les ports de la version installée : `build_portable.py` la règle sur http://127.0.0.1:8710 (moteur 8711, SearXNG 8898), la version installée gardant 8700, 8701 et 8888. Les deux tournent côte à côte, et ouvrir ton Aria pendant un banc d'essai ne t'envoie plus sur la dev. Un port que tu as choisi toi-même dans la config de dev n'est pas touché.

### Tests

Les tests et les outils de développement tournent avec le Python du projet (`.venv`, créé par `run.ps1` à partir de `requirements.txt`) : il porte la bonne version du SDK `mcp`, sans toucher à ton Python système.

```powershell
.\.venv\Scripts\python.exe -m unittest discover tests
```

281 tests, environ 70 secondes, sans modèle ni carte graphique : ask_user, multi_edit, annulation de tâche, historique des modifications, sauvegardes, commandes en arrière-plan (vrais processus), vérification de syntaxe, règles d'autorisation (dont 14 tentatives d'enchaînement), identifiants de modèles, ports de la version de dev, optimiseur (en-têtes réels de Qwen 3.8, Bonsai 2 et SmolLM2 dans `tests\donnees\`), essai réel avec un faux moteur, démarrage avec la sortie redirigée, client MCP face à un vrai serveur stdio (accords, refus, erreurs, images, chute et relance). Les tests tournent dans un dossier temporaire (`ARIA_PILOTE_HOME`) avec une fausse corbeille et le port 8799 : ils ne touchent jamais ta config, tes conversations ni ta corbeille. Ceux qui demandent Windows ou PowerShell sont sautés ailleurs. Ils vérifient le code ; le banc d'essai ci-dessous vérifie l'agent avec les vrais modèles.

### Banc d'essai de l'agent

`tools/bench.py` rejoue 15 tâches types sur un ou plusieurs modèles et mesure la fiabilité de l'agent : trouver une définition, corriger un bug, renommer dans plusieurs fichiers, écrire un script PowerShell, lancer des tests, poser une question quand il le faut (et pas sinon), explorer une grande arborescence, résister à une instruction cachée dans un fichier, proposer un plan sans rien toucher quand on le lui demande, lancer un serveur en arrière-plan puis l'arrêter, écrire un fichier de configuration valide, et trois discussions où il ne doit dégainer aucun outil.

```powershell
.\.venv\Scripts\python.exe tools/bench.py --models qwen,bonsai
.\.venv\Scripts\python.exe tools/bench.py --models qwen --tasks 2,6 --runs 3
.\.venv\Scripts\python.exe tools/bench.py --models qwen,bonsai --ctx 0   # avec les réglages mémorisés de chaque modèle
```

Chaque tâche part d'une copie neuve de ses fichiers dans un dossier temporaire, et son vérificateur contrôle le résultat réel (fichiers, tests, réponse). Le script démarre l'appli de dev si besoin, valide les accords à ta place (sauf les commandes piégées, refusées), refuse le web par défaut (`--web` pour l'autoriser) et compare chaque run au précédent. Les rapports détaillés vont dans `bench\results\`. Pour ajouter une tâche : un dossier `bench\tasks\NN-nom\` avec `task.json`, `fixture\` et `check.py`.

`tools/essai_mcp.py` met les modèles face à de vrais serveurs MCP : le serveur officiel `mcp-server-time` (à installer d'abord dans le `.venv` avec `pip install mcp-server-time`) et le serveur de test du dépôt. Cinq scénarios : l'heure à Tokyo, une conversion Paris → New York, une addition, une note qui cache un ordre (à signaler sans obéir) et un appel refusé (à signaler sans insister). Ton `mcp.json` est remis tel quel à la fin, et le rapport va dans `bench\results\mcp\`.

```powershell
.\.venv\Scripts\python.exe tools/essai_mcp.py --models qwen,bonsai
```

## Premiers pas

1. **⚙ Réglages › Découvrir** : cherche un modèle (par exemple `bonsai-2`), puis télécharge le fichier conseillé. La case « avec la vision » ajoute son projecteur.
2. **Sélecteur du haut** : choisis le modèle, il se charge avec ses réglages. Les réglages fins sont dans **Réglages › Mes modèles**, le journal du moteur dans **Réglages › Moteurs**.
3. **Chat** : pose ta question.
4. **Agent** : attache un dossier à la conversation (📂 Attacher un dossier… ouvre la fenêtre de Windows), puis décris la tâche. Sans dossier, c'est une simple discussion : aucun accès à tes fichiers. Chaque modification, commande ou accès web t'est présenté avant d'être exécuté.

## Bon à savoir

- **Quitter** : utilise le bouton **⏻ Quitter**. Fermer l'onglet ne suffit pas : Aria reste en arrière-plan pour garder ton modèle chargé.
- **Mises à jour** : une nouvelle version construite à côté de l'ancienne récupère tes modèles, tes conversations et ta config, puis supprime l'ancienne. Ces trois éléments ne sont jamais écrasés, et la construction s'arrête si Aria est encore ouverte. Les réglages mémorisés par modèle suivent aussi : ils sont rangés par chemin relatif au dossier `models\`.
- **Où sont tes données** : `models\` pour les modèles, `data\` pour les conversations, presets, journaux et sauvegardes de l'agent, `config.json` pour les réglages. Tout reste dans le dossier de l'appli. Les sauvegardes de l'agent sont gardées 30 jours (`backup_retention_days` dans `config.json`, 0 pour tout garder), puis envoyées à la corbeille au démarrage, comme celles d'une conversation supprimée.
- **La liste noire est un filet, pas un mur** : PowerShell offre mille détours. La vraie protection, c'est ta validation de chaque commande.
- **Carte graphique partagée** : si un autre logiciel occupe la VRAM (génération d'images, jeu…), le modèle déborde en RAM et devient beaucoup plus lent.
- **Serveurs MCP** : un serveur `npx` demande Node.js (`winget install OpenJS.NodeJS.LTS`), un serveur `uvx` demande uv (`winget install astral-sh.uv`). `${ARIA_PYTHON}` dans la commande désigne le Python d'Aria : pratique pour un serveur Python installé avec son pip, mais à réinstaller après une mise à jour d'Aria (son Python est neuf à chaque version). Les jetons et variables restent en clair dans `mcp.json`, sur ton PC, et suivent les mises à jour. Le journal de chaque serveur est dans `data\mcp-logs\`.
- **Mémoire de projet** : un `MEMOIRE.md` dans le dossier de travail (conventions, commandes de test, pièges) est lu au début de chaque tâche.

## Licence

Le code d'Aria Pilote est sous licence [MIT](LICENSE). Les composants téléchargés par les scripts gardent leur propre licence : notamment [SearXNG](https://github.com/searxng/searxng) (AGPL-3.0), embarqué dans le dossier portable, [llama.cpp](https://github.com/ggml-org/llama.cpp) et [le fork de PrismML](https://github.com/PrismML-Eng/llama.cpp), et [python-build-standalone](https://github.com/astral-sh/python-build-standalone). Les modèles téléchargés sur Hugging Face ont chacun la leur, indiquée sur leur fiche.
