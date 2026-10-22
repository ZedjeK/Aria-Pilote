"""Petit serveur de supervision : GET /statut renvoie l'état et un code de contrôle."""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REGLAGES = json.loads((Path(__file__).parent / "serveur.json").read_text(encoding="utf-8"))


class Gestionnaire(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/statut":
            self.send_error(404)
            return
        corps = json.dumps({"etat": "ok", "code": REGLAGES["code"]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)


if __name__ == "__main__":
    serveur = HTTPServer(("127.0.0.1", REGLAGES["port"]), Gestionnaire)
    print(f"Serveur prêt sur http://127.0.0.1:{REGLAGES['port']}")
    serveur.serve_forever()
