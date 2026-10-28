"""Test de sélection des compétences : le modèle charge-t-il tout seul la bonne (ou aucune) ?

Chaque phrase part dans une conversation neuve, sans dossier et « Sans persona », sur l'appli de dev. On note la
compétence chargée avec load_skill au premier tour (ou « aucune » s'il répond directement), puis on coupe.

    .venv/Scripts/python.exe tools/selection.py --models qwen,bonsai
    .venv/Scripts/python.exe tools/selection.py --models bonsai --only dev,prof
"""

import argparse
import json
import shutil
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench import BASE, ORIGIN, alive, find_model, load_model  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from aria_pilote.skills import normalize  # noqa: E402

NONE = "aucune"
GOAL = 0.9

# (phrase, compétence attendue) ; quelques phrases admettent une seconde réponse juste (tuple)
CASES = [
    ("Mon script Python plante avec KeyError: 'port' quand je lance python app.py, tu peux corriger ?", "dev"),
    ("Écris-moi une fonction Python qui vérifie si une adresse IP appartient à un sous-réseau donné.", "dev"),
    ("Mon nginx renvoie 502 depuis que j'ai changé le port de l'appli, ma conf : proxy_pass http://127.0.0.1:3000;",
     ("dev", "depannage")),  # panne de service ou config à corriger : les deux se défendent
    ("Comment je découpe cette fonction JavaScript de 200 lignes sans rien casser ?", "dev"),
    ("Je dois migrer 40 postes vers Windows 11 avant fin mars, aide-moi à organiser ça.", "projet"),
    ("J'ai trop de choses cette semaine : la mise à jour du pare-feu, deux tickets clients et un audit. Par quoi je commence ?", "projet"),
    ("Je veux lancer une chaîne YouTube sur mon homelab d'ici trois mois, fais-moi un plan.", "projet"),
    ("Planifie le déménagement de notre salle serveur vers le nouveau bâtiment.", "projet"),
    ("J'ai reçu un mail de « Microsoft » qui me demande de valider mon compte via un lien bizarre, c'est du phishing ?", "securite"),
    ("Mon serveur web ouvre des milliers de connexions sortantes vers une IP inconnue, qu'est-ce que je fais ?", "securite"),
    ("J'ai poussé par erreur ma clé AWS dans un dépôt GitHub public il y a une heure.", "securite"),
    ("Comment durcir un serveur Debian exposé sur Internet ?", "securite"),
    ("C'est quoi la différence entre TCP et UDP ? Je ne comprends jamais.", "prof"),
    ("Explique-moi comment marche le protocole OSPF.", "prof"),
    ("J'ai un exercice : trouver le masque d'un réseau de 500 hôtes. Aide-moi sans me donner la réponse.", "prof"),
    ("Je révise la CCNA, interroge-moi sur le trunking des VLAN.", "prof"),
    ("Écris un mail à mon chef pour lui demander un jour de télétravail de plus par semaine.", "redaction"),
    ("Corrige ce message avant que je l'envoie : « Bonjour, je vous ecrit pour vous informé que la maintenance est decaler a jeudi. »", "redaction"),
    ("Rédige le compte rendu de la réunion d'hier : on passe à Proxmox, Paul fait le devis pour vendredi.", "redaction"),
    ("Aide-moi à améliorer mon CV d'admin système pour un poste d'ingénieur réseau.", "redaction"),
    ("Proxmox ou VMware ESXi pour mon homelab, lequel je prends ?", "analyste"),
    ("Est-ce que ça vaut le coup de passer à une fibre pro à 80 € par mois pour une PME de 10 personnes ?", "analyste"),
    ("Compare Bitwarden, 1Password et KeePassXC pour une petite équipe.", "analyste"),
    ("Nos tickets support sont passés de 120 en janvier à 156 en février, après la mise à jour. Tu en conclus quoi ?", "analyste"),
    ("Trouve-moi un nom pour mon homelab, dans un style Warhammer 40k.", "creatif"),
    ("Écris une courte nouvelle de science-fiction sur une IA qui garde un phare.", "creatif"),
    ("On fait un jeu de rôle : tu es une marchande dans une cité cyberpunk, je viens t'acheter une puce illégale.", "creatif"),
    ("Invente un slogan pour mon entreprise de dépannage informatique.", "creatif"),
    ("Relis ce code et dis-moi tout ce qui cloche : def get_user(id): return db.execute('SELECT * FROM users WHERE id=' + id)", "revue-code"),
    ("Fais une revue de mon script bash de sauvegarde avant la prod : tar czf /backup/$1.tgz /home && rm -rf /tmp/$2", "revue-code"),
    ("Tu peux faire une revue de code de cette fonction ? function sum(a){let t;for(i=0;i<=a.length;i++)t+=a[i];return t}", "revue-code"),
    ("Liste les défauts de ce Dockerfile : FROM ubuntu / RUN apt-get update / RUN apt-get install -y python3 / ENV PASSWORD=admin123", "revue-code"),
    ("Écris un script PowerShell qui supprime les profils utilisateurs inactifs depuis plus de 90 jours.", "script-powershell"),
    ("J'ai besoin d'un script PowerShell pour créer des comptes AD à partir d'un CSV.", "script-powershell"),
    ("Fais-moi un .ps1 qui vérifie l'espace disque de mes serveurs et m'alerte sous 10 %.", "script-powershell"),
    ("Corrige mon script PowerShell : Get-ChildItem C:\\Logs | where {$_.LastWriteTime -lt (Get-Date).AddDays(-30)} | rm", "script-powershell"),
    ("Prépare-moi un message de commit pour mes modifications en cours.", "message-commit"),
    ("Écris le message de commit pour ce changement : j'ai ajouté la reprise des téléchargements interrompus dans downloader.py.", "message-commit"),
    ("Comment je formule le commit pour ce diff ? « - timeout = 10 » « + timeout = 30 » dans config.py", "message-commit"),
    ("Rédige un message de commit pour la correction du bug d'encodage des noms de fichiers.", "message-commit"),
    ("Crée-moi une compétence pour écrire mes playbooks Ansible.", "createur-competence"),
    ("Je veux une nouvelle compétence qui rédige mes comptes rendus d'intervention toujours de la même façon.", "createur-competence"),
    ("Ma compétence revue-ansible ne se déclenche jamais, aide-moi à améliorer sa description.", "createur-competence"),
    ("Aide-moi à écrire un SKILL.md pour préparer mes rapports de sauvegarde hebdomadaires.", "createur-competence"),
    ("Résume-moi cet article : « Proxmox VE 9 passe à Debian 13, ajoute les instantanés sur stockage LVM partagé, "
     "améliore la migration à chaud et abandonne les anciens noyaux 6.2. La mise à jour depuis la 8 se fait avec "
     "pve8to9 puis apt. »", "resume"),
    ("Fais-moi un résumé de ce fil de mails : « Paul : le NAS ne répond plus depuis ce matin. Marie : le disque 3 est "
     "en panne, j'en commande un. Paul : on le change quand ? Marie : samedi 9 h, il faudra couper l'accès. »", "resume"),
    ("Voici les notes de la réunion d'hier, résume l'essentiel : « budget validé pour deux switchs ; Karim teste le "
     "VLAN invités d'ici mardi ; la migration de la messagerie est repoussée à janvier ; question des licences en "
     "suspens. »", "resume"),
    ("Résume ce long message en trois points : « Bonjour, suite à l'audit, il faut changer les mots de passe admin, "
     "activer la double authentification sur le VPN, mettre à jour les deux pare-feu avant la fin du mois et "
     "documenter les accès des prestataires. Merci. »", "resume"),
    ("Depuis ce matin, plus personne n'accède au partage réseau du NAS, mais internet fonctionne. Qu'est-ce que je "
     "vérifie ?", "depannage"),
    ("Mon serveur Debian est devenu très lent depuis hier soir, la charge monte à 30.", "depannage"),
    ("Le Wi-Fi coupe toutes les dix minutes dans le bureau du fond, les autres pièces vont bien.", "depannage"),
    ("Windows Update échoue avec l'erreur 0x80070005 sur trois postes du service compta.", "depannage"),
    ("Traduis ce mail en anglais pour le support Synology : le volume 1 est passé en mode dégradé après le "
     "remplacement du disque 2.", "traduction"),
    ("Traduis en français ce message d'erreur : « The specified network name is no longer available. »", "traduction"),
    ("Peux-tu relire ma traduction en anglais ? « We have eventually rebooted the server yesterday. »", "traduction"),
    ("Translate this to French for my team: « Please make sure to back up your files before Friday's maintenance "
     "window. »", "traduction"),
    ("Prépare le MEMOIRE.md de ce projet.", "memoire-projet"),
    ("Mets à jour la mémoire du projet : on est passés de unittest à pytest.", "memoire-projet"),
    ("Écris le fichier mémoire qui explique à l'agent comment installer, lancer et tester ce dépôt.", "memoire-projet"),
    ("Analyse ce dossier et note ses conventions de code pour tes prochaines tâches.", "memoire-projet"),
    ("Salut ! Ça va aujourd'hui ?", NONE),
    ("Merci, c'était parfait.", NONE),
    ("Quelle est la capitale du Japon ?", NONE),
    ("Combien font 17 fois 23 ?", NONE),
    ("J'ai passé une sale journée, j'ai juste besoin de parler un peu.", NONE),
    ("Tu préfères Blade Runner ou Ghost in the Shell ?", NONE),
    ("Quel port utilise SSH par défaut ?", NONE),
    ("Bonne nuit, à demain !", NONE),
]


# Compétences qui n'ont de sens qu'avec un dossier attaché : leurs phrases tournent dans un petit projet de test
FOLDER_SKILLS = {"memoire-projet"}
FIXTURE = {
    "README.md": "# Facturier\n\nPetit outil qui génère les factures PDF du mois à partir d'un CSV.\n",
    "requirements.txt": "reportlab>=4.0\n",
    "facturier/__init__.py": "",
    "facturier/main.py": "# Point d'entrée : py -m facturier factures.csv\n",
    "tests/test_main.py": "import unittest\n",
}


def _workspace():
    folder = Path(tempfile.mkdtemp(prefix="aria-selection-"))
    for rel, text in FIXTURE.items():
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_text(text, encoding="utf-8")
    return folder


def _answers(expected):
    """Réponses justes d'une phrase : une compétence, ou un tuple quand deux se défendent."""
    return expected if isinstance(expected, tuple) else (expected,)


def pick(client, prompt, timeout, folder=False):
    """Compétence chargée au premier tour : la première load_skill, ou « aucune » si le modèle répond sans."""
    conv = client.post("/api/conversations", json={"persona_id": "none"}).json()
    cid = conv["id"]
    ws = _workspace() if folder else None
    patch = {"title": "[sélection]"}
    if ws:
        patch["agent"] = {"enabled": True, "workspace": str(ws)}
    client.patch(f"/api/conversations/{cid}", json=patch)
    chosen, others, deadline = NONE, [], time.time() + timeout
    try:
        with client.stream("POST", "/api/chat", json={"conversation_id": cid, "content": prompt},
                           timeout=httpx.Timeout(30, read=None)) as resp:
            if resp.status_code != 200:
                # Moteur arrêté ou refus : ce n'est pas un choix du modèle, on le dit
                return f"erreur {resp.status_code}", []
            for line in resp.iter_lines():
                if time.time() > deadline:
                    chosen = "délai"
                    break
                if not line.startswith("data: "):
                    continue
                ev = json.loads(line[6:])
                if ev["type"] == "tool_call":
                    if ev["name"] == "load_skill":
                        chosen = normalize((ev.get("args") or {}).get("name") or "") or "?"
                        break  # fermer le flux arrête la génération
                    others.append(ev["name"])
                    if len(others) >= 3:
                        break
                elif ev["type"] == "approval":
                    client.post("/api/agent/approve", json={"approval_id": ev["approval_id"], "decision": "deny"})
                elif ev["type"] == "done":
                    break  # réponse finie sans load_skill (les appels d'un tour arrivent après son step_end)
    finally:
        client.delete(f"/api/conversations/{cid}", headers=ORIGIN)
        if ws:
            shutil.rmtree(ws, ignore_errors=True)
    return chosen, others


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", required=True, help="motifs de noms, séparés par des virgules")
    parser.add_argument("--ctx", type=int, default=0, help="0 = réglages mémorisés du modèle")
    parser.add_argument("--only", default="", help="compétences attendues à tester (ex. dev,aucune)")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--out", default="", help="fichier JSON des résultats")
    args = parser.parse_args()
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    cases = [c for c in CASES if not only or set(_answers(c[1])) & only]
    report = {}
    with httpx.Client(base_url=BASE, timeout=60) as client:
        if not alive(client):
            sys.exit(f"L'appli de dev ne répond pas sur {BASE}.")
        for pattern in args.models.split(","):
            model = find_model(client, pattern.strip())
            load_model(client, model, args.ctx)
            rows, misses = [], Counter()
            for i, (prompt, expected) in enumerate(cases, 1):
                started = time.time()
                chosen, others = pick(client, prompt, args.timeout, folder=bool(set(_answers(expected)) & FOLDER_SKILLS))
                ok = chosen in _answers(expected)
                expected = "|".join(_answers(expected))
                rows.append({"prompt": prompt, "expected": expected, "chosen": chosen, "others": others, "ok": ok,
                             "s": round(time.time() - started, 1)})
                if not ok:
                    misses[expected] += 1
                print(f"[{i:02d}/{len(cases)}] {'✓' if ok else '✗'} attendu {expected:<17} choisi {chosen:<17}"
                      f"{' (+' + ','.join(others) + ')' if others else ''}  {prompt[:60]}", flush=True)
            rate = sum(r["ok"] for r in rows) / len(rows)
            print(f"=== {Path(model['path']).name} : {rate:.0%} ({sum(r['ok'] for r in rows)}/{len(rows)})"
                  f"{' — objectif atteint' if rate >= GOAL else ' — sous l objectif'}; ratés : {dict(misses)}", flush=True)
            report[Path(model["path"]).name] = {"rate": rate, "rows": rows}
    if args.out:
        Path(args.out).write_text(json.dumps({"date": datetime.now().isoformat(timespec="seconds"), "models": report},
                                             ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
