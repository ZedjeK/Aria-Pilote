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
  <a href="#compiler-soi-même"><b>Compiler Aria Pilote</b></a> · v0.9 · Windows 10/11 x64 · carte NVIDIA conseillée · licence MIT (code d'Aria) / AGPL-3.0 (SearXNG embarqué)
</p>

## Ce qu'il fait

| Fonction | Ce que ça fait | Moteur |
| --- | --- | --- |
| **Modèles locaux** | Trouve tes fichiers GGUF et lit leur en-tête : quantification, contexte maximal, nombre de couches, projecteur vision. Contexte, couches GPU, threads, flash attention et cache de contexte (normal, ou compressé q8_0 / q4_0 pour un contexte plus grand) se règlent et se mémorisent par modèle | llama.cpp |
| **Optimiser mes modèles** | Dans les Réglages : scan du PC (carte graphique et VRAM libre, RAM, processeur), puis calcul pour chaque modèle des meilleurs réglages d'après son en-tête GGUF — tout sur le GPU si possible, le plus grand contexte qui tient, cache compressé seulement s'il fait gagner un palier. Profil Équilibré (jusqu'à 32k) ou Contexte max. Aperçu avant d'appliquer | — |
| **Bonsai 2 et formats ternaires** | Les fichiers PQ2_0 et PTQ1_0 de PrismML partent tout seuls sur le bon moteur. Si le moteur officiel refuse un fichier, Aria bascule et retient le choix | Fork PrismML de llama.cpp |
| **Découvrir** | Recherche sur Hugging Face, fichiers d'un dépôt avec leur taille et une estimation « tient sur le GPU / GPU + RAM / trop gros ». Téléchargement avec reprise, vérification SHA-256 et rangement automatique. Un modèle supprimé part à la corbeille | Hugging Face |
| **Chat** | Réponses en streaming, presets (prompt système, échantillonnage, réflexion activée ou non), réflexion du modèle repliable, Markdown avec blocs de code copiables, historique des conversations | llama-server (API compatible OpenAI) |
| **Agent** | Dans le dossier que tu lui donnes : lister, lire, chercher, modifier des fichiers et lancer des commandes PowerShell, y compris en arrière-plan (un serveur qu'il lance, interroge puis arrête ; arrêté de toute façon en fin de tâche, sauf si tu lui demandes de le laisser tourner). Chaque modification arrive avec son diff, chaque fichier est sauvegardé avant d'être touché, et « Annuler cette modif » le remet comme avant. « Annuler toute la tâche » remet d'un coup tous les fichiers de la tâche dans leur état d'avant (les versions actuelles partent à la corbeille, les fichiers retouchés depuis sont signalés et laissés tels quels, les commandes lancées sont rappelées car elles ne s'annulent pas) | Outils d'Aria |
| **Garde-fous** | Ton accord avant chaque modification, commande ou accès web. Pour une commande sûre que tu valides souvent, « Toujours autoriser dans ce projet » retient la commande exacte ou son début (`python -m unittest*`) ; une règle ne couvre jamais une suite avec `;` `&` `|` `>` `$` ou un retour à la ligne, elle est stockée côté Aria (jamais dans le projet) et le bouton **Règles** de la barre Agent permet de les retirer. Dossier de travail cloisonné, liens et jonctions compris. Liste noire de commandes refusées d'office (formatage, registre système, désactivation de l'antivirus, télécharger-puis-exécuter…). 25 appels d'outils au plus par tâche. Après chaque écriture, la syntaxe du fichier est vérifiée sans l'exécuter (Python, JSON, YAML, TOML, XML, PowerShell) et l'agent voit tout de suite la ligne fautive (`"auto_check": false` dans `config.json` pour désactiver) | — |
| **Plan et mémoire** | L'agent écrit son plan d'étapes et le coche au fil du travail. Un fichier `ARIA.md` à la racine d'un projet lui donne tes conventions à chaque tâche | — |
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
| `build_portable.py` | Assemble `..\Aria-Pilote-0.9.0\` : Python portable vérifié par SHA-256, code, moteurs, SearXNG et le lanceur `Aria-Pilote.exe` |

Lance ensuite `Aria-Pilote.exe`. L'exe n'est pas signé : si Windows SmartScreen affiche « Windows a protégé votre ordinateur », clique sur « Informations complémentaires » puis « Exécuter quand même ».

Pour développer sans construire : `run.ps1` lance l'appli depuis le code source.

### Banc d'essai de l'agent

`tools/bench.py` rejoue 12 tâches types sur un ou plusieurs modèles et mesure la fiabilité de l'agent : trouver une définition, corriger un bug, renommer dans plusieurs fichiers, écrire un script PowerShell, lancer des tests, poser une question quand il le faut (et pas sinon), explorer une grande arborescence, résister à une instruction cachée dans un fichier, respecter le mode Plan, lancer un serveur en arrière-plan puis l'arrêter, écrire un fichier de configuration valide.

```powershell
py -3.11 tools/bench.py --models qwen,bonsai
py -3.11 tools/bench.py --models qwen --tasks 2,6 --runs 3
py -3.11 tools/bench.py --models qwen,bonsai --ctx 0   # avec les réglages mémorisés de chaque modèle
```

Chaque tâche part d'une copie neuve de ses fichiers dans un dossier temporaire, et son vérificateur contrôle le résultat réel (fichiers, tests, réponse). Le script démarre l'appli de dev si besoin, valide les accords à ta place (sauf les commandes piégées, refusées), refuse le web par défaut (`--web` pour l'autoriser) et compare chaque run au précédent. Les rapports détaillés vont dans `bench\results\`. Pour ajouter une tâche : un dossier `bench\tasks\NN-nom\` avec `task.json`, `fixture\` et `check.py`.

## Premiers pas

1. **Découvrir** : cherche un modèle (par exemple `bonsai-2`), puis télécharge le fichier conseillé. La case « avec la vision » ajoute son projecteur.
2. **Modèles** : choisis-le et clique sur **Charger**. Le journal du moteur s'affiche en direct.
3. **Chat** : pose ta question.
4. **Agent** : coche **Agent**, indique un dossier de travail, puis décris la tâche. Chaque modification, commande ou accès web t'est présenté avant d'être exécuté.

## Bon à savoir

- **Quitter** : utilise le bouton **⏻ Quitter**. Fermer l'onglet ne suffit pas : Aria reste en arrière-plan pour garder ton modèle chargé.
- **Mises à jour** : une nouvelle version construite à côté de l'ancienne récupère tes modèles, tes conversations et ta config, puis supprime l'ancienne. Ces trois éléments ne sont jamais écrasés, et la construction s'arrête si Aria est encore ouverte. Les réglages mémorisés par modèle suivent aussi : ils sont rangés par chemin relatif au dossier `models\`.
- **Où sont tes données** : `models\` pour les modèles, `data\` pour les conversations, presets, journaux et sauvegardes de l'agent, `config.json` pour les réglages. Tout reste dans le dossier de l'appli. Les sauvegardes de l'agent sont gardées 30 jours (`backup_retention_days` dans `config.json`, 0 pour tout garder), puis envoyées à la corbeille au démarrage, comme celles d'une conversation supprimée.
- **La liste noire est un filet, pas un mur** : PowerShell offre mille détours. La vraie protection, c'est ta validation de chaque commande.
- **Carte graphique partagée** : si un autre logiciel occupe la VRAM (génération d'images, jeu…), le modèle déborde en RAM et devient beaucoup plus lent.
- **Mémoire de projet** : un `ARIA.md` dans le dossier de travail (conventions, commandes de test, pièges) est lu au début de chaque tâche.

## Licence

Le code d'Aria Pilote est sous licence [MIT](LICENSE). Les composants téléchargés par les scripts gardent leur propre licence : notamment [SearXNG](https://github.com/searxng/searxng) (AGPL-3.0), embarqué dans le dossier portable, [llama.cpp](https://github.com/ggml-org/llama.cpp) et [le fork de PrismML](https://github.com/PrismML-Eng/llama.cpp), et [python-build-standalone](https://github.com/astral-sh/python-build-standalone). Les modèles téléchargés sur Hugging Face ont chacun la leur, indiquée sur leur fiche.
