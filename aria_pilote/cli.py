"""Ligne de commande : `python -m aria_pilote.cli -p "ta demande"` parle à l'Aria Pilote qui tourne déjà.

Rien n'est lancé ni chargé ici : si l'appli est fermée, ou si aucun modèle n'est prêt, la commande le dit et s'arrête.
Elle ne décide jamais à ta place : une action qui demande ton accord est refusée (code 3) et dit quoi autoriser dans
l'interface, une question de l'agent arrête la commande (code 4), un plan proposé aussi (code 6).

Codes de sortie : 0 réussi · 1 erreur · 2 appli injoignable ou aucun modèle prêt · 3 accord refusé · 4 question en
attente · 5 délai dépassé · 6 plan en attente de ta décision.

Pas de jeton : comme l'interface, la commande passe par 127.0.0.1 sur le port de la config, sans en-tête Origin. Un
programme lancé sous ton compte peut donc faire la même chose qu'elle (c'est déjà le cas de l'API aujourd'hui).
"""

import argparse
import json
import os
import sys
import threading

import httpx

from . import config

OK, ERROR, UNREACHABLE, APPROVAL, QUESTION, TIMEOUT, PLAN = 0, 1, 2, 3, 4, 5, 6
DEFAULT_TIMEOUT = 600  # secondes, pour toute la tâche
SHOWN = 100  # caractères d'un argument d'outil dans le suivi


class CliError(Exception):
    def __init__(self, message, code=ERROR):
        super().__init__(message)
        self.code = code


def base_url():
    return f"http://127.0.0.1:{config.get()['app_port']}"


def _say(text):
    print(text, file=sys.stderr, flush=True)


def _short(value):
    text = " ".join(str(value).split())
    return text if len(text) <= SHOWN else text[:SHOWN - 1] + "…"


def _detail(args):
    """L'argument qui dit le mieux ce que fait l'outil, pour le suivi."""
    for key in ("command", "path", "paths", "pattern", "query", "url", "question"):
        if args.get(key):
            return _short(args[key])
    return ""


def check_ready(client):
    """L'appli répond et un modèle est prêt, sinon une erreur qui dit quoi faire."""
    try:
        response = client.get("/api/status", timeout=5)
    except httpx.HTTPError:
        raise CliError("Aria Pilote ne répond pas sur " + base_url() + " : lance-la d'abord, la commande ne la démarre pas.",
                       UNREACHABLE)
    try:
        status = response.json() if response.status_code == 200 else None
    except ValueError:
        status = None
    if status is None:
        raise CliError(f"Aria Pilote répond mal sur {base_url()} (HTTP {response.status_code}) : regarde son journal.")
    if status.get("state") != "ready":
        raise CliError("Aucun modèle n'est prêt dans Aria Pilote (état : " + str(status.get("state")) + ") : charge-en un "
                       "dans l'interface, la commande n'en charge pas.", UNREACHABLE)


def _error_detail(response):
    try:
        return response.json().get("detail") or response.text
    except ValueError:
        return response.text


def new_conversation(client, folder):
    body = {"agent": {"enabled": True, "workspace": os.path.abspath(folder)}} if folder else {}
    response = client.post("/api/conversations", json=body)
    if response.status_code != 200:
        raise CliError(f"Conversation impossible : {_error_detail(response)}")
    return response.json()["id"]


def _deny(client, approval_id):
    try:
        client.post("/api/agent/approve", json={"approval_id": approval_id, "decision": "deny"})
    except httpx.HTTPError:
        pass  # la tâche s'arrêtera de toute façon avec la fin du flux


def ask(client, conversation_id, prompt, plan_first=False, timeout=DEFAULT_TIMEOUT, quiet=False):
    """Envoie la demande et lit le flux jusqu'au bout. Renvoie le compte rendu (voir `exit_code`)."""
    result = {"conversation": conversation_id, "reponse": "", "outils": [], "refus": [], "question": None,
              "plan": None, "erreur": None, "delai": False}
    names, step_text = {}, []
    body = {"conversation_id": conversation_id, "content": prompt, "plan_first": plan_first}
    expired = threading.Event()
    with client.stream("POST", "/api/chat", json=body, timeout=httpx.Timeout(10, read=None)) as response:
        if response.status_code != 200:
            response.read()
            raise CliError(f"Demande refusée : {_error_detail(response)}")
        timer = threading.Timer(timeout, lambda: (expired.set(), response.close()))
        timer.daemon = True
        timer.start()
        try:
            for line in response.iter_lines():
                if not line.startswith("data: "):
                    continue
                try:
                    event = json.loads(line[6:])
                except ValueError:
                    continue
                kind = event.get("type")
                if kind == "delta":
                    step_text.append(event.get("content") or "")
                elif kind == "step_end":
                    step_text = []
                elif kind == "tool_call":
                    names[event["call_id"]] = (event["name"], event.get("args") or {})
                    if not quiet:
                        _say(f"▸ {event['name']} {_detail(event.get('args') or {})}".rstrip())
                elif kind == "tool_result":
                    name, args = names.get(event.get("call_id"), ("?", {}))
                    result["outils"].append({"nom": name, "statut": event.get("status")})
                    if name == "ask_user" and event.get("status") == "pending":
                        result["question"] = {"question": args.get("question", ""), "options": args.get("options") or []}
                    elif not quiet and event.get("status") not in ("ok", None):
                        _say(f"  ↳ {event['status']}")
                elif kind == "approval":
                    what = event.get("command") or event.get("path") or event.get("preview") or event.get("kind")
                    result["refus"].append({"type": event.get("kind"), "action": _short(what)})
                    if not quiet:
                        _say(f"  ✋ accord demandé, refusé : {_short(what)}")
                    _deny(client, event["approval_id"])
                elif kind == "plan":
                    result["plan"] = event.get("plan") or ""
                elif kind == "error":
                    result["erreur"] = event.get("message") or "erreur"
                elif kind == "done":
                    result["reponse"] = (event.get("message") or {}).get("content") or ""
        except (httpx.StreamClosed, httpx.ReadError, httpx.RemoteProtocolError):
            if not expired.is_set():
                raise CliError("La connexion avec Aria Pilote s'est coupée en cours de route.")
        finally:
            timer.cancel()
    result["delai"] = expired.is_set()
    if not result["reponse"]:
        result["reponse"] = "".join(step_text).strip()
    return result


def exit_code(result):
    if result["erreur"]:
        return ERROR
    if result["delai"]:
        return TIMEOUT
    if result["refus"]:
        return APPROVAL
    if result["question"]:
        return QUESTION
    if result["plan"] is not None:
        return PLAN
    return OK


def explain(result):
    """Ce que la commande ajoute sur la sortie d'erreur quand tout ne s'est pas passé comme prévu."""
    lines = []
    if result["erreur"]:
        lines.append(f"Erreur : {result['erreur']}")
    if result["delai"]:
        lines.append("Délai dépassé : la tâche a été interrompue (--delai pour l'allonger).")
    if result["refus"]:
        lines.append("Accord refusé : cette commande ne répond jamais oui à ta place. Actions refusées : "
                     + " ; ".join(f"{r['action']}" for r in result["refus"]) + ". Pour les autoriser, ajoute une règle "
                     "« Toujours autoriser » ou règle la conversation dans l'interface, puis reprends avec "
                     f"--conversation {result['conversation']}.")
    if result["question"]:
        options = result["question"]["options"]
        lines.append("Question de l'agent : " + result["question"]["question"]
                     + (" (" + " / ".join(map(str, options)) + ")" if options else "")
                     + f" Réponds avec --conversation {result['conversation']} -p \"ta réponse\".")
    if result["plan"] is not None:
        lines.append("Plan proposé, rien n'a été modifié : approuve-le dans l'interface (la conversation "
                     f"{result['conversation']}), ou reprends avec --conversation pour le corriger.")
    return lines


def main(argv=None, client=None):
    parser = argparse.ArgumentParser(prog="aria", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-p", "--prompt", required=True, help="la demande (« - » : lue sur l'entrée standard)")
    parser.add_argument("--dossier", help="dossier de travail d'une nouvelle conversation (refusé avec --conversation)")
    parser.add_argument("--conversation", help="continue cette conversation (son identifiant est donné à la fin de chaque essai)")
    parser.add_argument("--plan", action="store_true", help="l'agent propose d'abord un plan, sans rien modifier")
    parser.add_argument("--json", action="store_true", help="un objet JSON unique sur la sortie standard")
    parser.add_argument("--delai", type=int, default=DEFAULT_TIMEOUT, help=f"durée maximale de la tâche, en secondes (défaut : {DEFAULT_TIMEOUT})")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")  # « ▸ » et les accents sur une console Windows
    prompt = sys.stdin.read() if args.prompt == "-" else args.prompt
    owned = client is None
    client = client or httpx.Client(base_url=base_url(), timeout=30)
    try:
        if not prompt.strip():
            raise CliError("La demande est vide.")
        if args.delai < 1:
            raise CliError("--delai doit être d'au moins 1 seconde.")
        if args.conversation and args.dossier:
            raise CliError("--dossier ne s'applique qu'à une nouvelle conversation : celle-ci a déjà le sien.")
        check_ready(client)
        cid = args.conversation or new_conversation(client, args.dossier)
        result = ask(client, cid, prompt, args.plan, args.delai, quiet=args.json)
    except CliError as exc:
        if args.json:
            print(json.dumps({"erreur": str(exc), "code": exc.code}, ensure_ascii=False))
        else:
            _say(str(exc))
        return exc.code
    finally:
        if owned:
            client.close()
    code = exit_code(result)
    if args.json:
        print(json.dumps({**result, "code": code}, ensure_ascii=False))
    else:
        if result["reponse"]:
            print(result["reponse"])
        for line in explain(result):
            _say(line)
        _say(f"conversation : {result['conversation']}")
    return code


if __name__ == "__main__":
    sys.exit(main())
