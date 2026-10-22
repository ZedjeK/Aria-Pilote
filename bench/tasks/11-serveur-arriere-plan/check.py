import json
import secrets
import socket
from pathlib import Path


def setup(ws):
    # Port libre et code aléatoire à chaque passage : impossible de deviner la réponse sans interroger le serveur
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    reglages = {"port": port, "code": "SUPERVISION-" + secrets.token_hex(3).upper()}
    (Path(ws) / "serveur.json").write_text(json.dumps(reglages), encoding="utf-8")


def listening(port):
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def check(ws, run):
    reglages = json.loads((Path(ws) / "serveur.json").read_text(encoding="utf-8"))
    if reglages["code"] not in run["final"]:
        return False, "le code renvoyé par /statut n'est pas dans la réponse"
    if listening(reglages["port"]):
        return False, "le serveur tourne encore à la fin de la tâche"
    stopped = any(c["name"] == "stop_command" for c in run["calls"])
    background = any(c["name"] == "run_command" and c["args"].get("background") for c in run["calls"])
    if not background:
        return True, "ok (sans arrière-plan)"
    return True, "ok" if stopped else "ok (arrêté en fin de tâche, pas par l'agent)"
