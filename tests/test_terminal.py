"""Terminal intégré : le WebSocket n'accepte que l'interface d'Aria Pilote (hôte, origine, jeton à usage unique),
et un vrai PowerShell tourne dans le dossier de travail de la conversation."""

import shutil
import sys
import time
import unittest

from base import BASE_URL, ORIGIN, client, new_conversation, workspace
from starlette.websockets import WebSocketDisconnect

from aria_pilote import jobs, terminal

HOST = BASE_URL.removeprefix("http://")
GOOD = {"host": HOST, "origin": BASE_URL}


class Access(unittest.TestCase):
    """Les trois verrous, sans lancer de terminal."""

    def setUp(self):
        self.root = workspace()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.cid = new_conversation(self.root)
        self.client = client()

    def token(self, cid=None):
        return self.client.post("/api/terminal/token", json={"conversation_id": cid or self.cid}, headers=ORIGIN)

    def refused(self, token, **changed):
        """Le serveur ferme sans accepter : aucun terminal n'est ouvert. Un seul en-tête s'écarte du bon cas.

        (Le client de test n'envoie pas l'hôte de l'appli de lui-même pour un WebSocket : on le donne.)"""
        headers = {k: v for k, v in {**GOOD, **changed}.items() if v is not None}
        with self.assertRaises(WebSocketDisconnect) as ctx:
            with self.client.websocket_connect(f"/api/terminal/ws?token={token}", headers=headers):
                pass
        self.assertEqual(ctx.exception.code, 1008)
        self.assertIsNone(terminal.get(self.cid))

    def test_hote_et_origine(self):
        self.assertIsNone(terminal.refusal(GOOD))
        self.assertIsNone(terminal.refusal({"host": f"localhost:{HOST.split(':')[1]}", "origin": f"http://localhost:{HOST.split(':')[1]}"}))
        self.assertEqual(terminal.refusal({"host": "evil.example", "origin": BASE_URL}), "Hôte refusé.")
        self.assertEqual(terminal.refusal({"host": HOST, "origin": "https://evil.example"}), "Origine refusée.")
        self.assertEqual(terminal.refusal({"host": HOST}), "Origine refusée.")  # absente : refusée aussi
        self.assertEqual(terminal.refusal({"host": HOST, "origin": "null"}), "Origine refusée.")

    def test_jeton_a_usage_unique_et_perissable(self):
        token = terminal.issue_token("abc")
        self.assertEqual(terminal.redeem_token(token), "abc")
        self.assertIsNone(terminal.redeem_token(token))  # déjà utilisé
        self.assertIsNone(terminal.redeem_token("inconnu"))
        self.assertIsNone(terminal.redeem_token(None))
        old = terminal.issue_token("abc")
        terminal._tokens[old] = ("abc", time.monotonic() - 1)
        self.assertIsNone(terminal.redeem_token(old))  # périmé

    def test_jeton_reserve_a_une_conversation_avec_dossier(self):
        self.assertEqual(self.token("inconnue").status_code, 404)
        self.assertEqual(self.token(new_conversation()).status_code, 409)
        resp = self.token()
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["running"])
        # Le jeton se demande par un POST : une autre page ne peut pas l'obtenir (local_only)
        evil = self.client.post("/api/terminal/token", json={"conversation_id": self.cid}, headers={"Origin": "https://evil.example"})
        self.assertEqual(evil.status_code, 403)

    def test_websocket_refuse_une_autre_page(self):
        token = self.token().json()["token"]
        self.refused(token, origin="https://evil.example")
        self.assertEqual(terminal.redeem_token(token), self.cid)  # refusé avant même de consommer le jeton

    def test_websocket_refuse_sans_origine_ou_mauvais_hote(self):
        self.refused(self.token().json()["token"], origin=None)
        self.refused(self.token().json()["token"], host="evil.example")

    def test_websocket_refuse_sans_jeton_valide(self):
        self.refused("")
        self.refused("invente")
        token = self.token().json()["token"]
        terminal.redeem_token(token)
        self.refused(token)  # rejoué

    def test_taille_bornee(self):
        self.assertEqual(terminal._clamp((30, 120)), (30, 120))
        self.assertEqual(terminal._clamp((0, 100000)), (terminal.MIN_SIZE[0], terminal.MAX_SIZE[1]))
        self.assertEqual(terminal._clamp((None, "x")), terminal.DEFAULT_SIZE)


@unittest.skipUnless(sys.platform == "win32", "pseudo-terminal Windows (ConPTY)")
class RealTerminal(unittest.TestCase):
    """Un vrai PowerShell, de bout en bout par le WebSocket."""

    def setUp(self):
        self.root = workspace()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.cid = new_conversation(self.root)
        self.client = client()
        self.addCleanup(terminal.stop_all)

    def connect(self):
        token = self.client.post("/api/terminal/token", json={"conversation_id": self.cid}, headers=ORIGIN).json()["token"]
        return self.client.websocket_connect(f"/api/terminal/ws?token={token}&rows=30&cols=100", headers=GOOD)

    def read_until(self, ws, marker, timeout=30):
        seen, deadline = "", time.time() + timeout
        while marker not in seen and time.time() < deadline:
            message = ws.receive_json()
            if message["t"] == "out":
                seen += message["d"]
            elif message["t"] in ("exit", "error"):
                self.fail(f"terminal arrêté : {message} après {seen[-300:]!r}")
        self.assertIn(marker, seen)
        return seen

    def test_commande_dans_le_dossier_de_travail_puis_reconnexion(self):
        (self.root / "témoin.txt").write_text("x", encoding="utf-8")
        with self.connect() as ws:
            # Le marqueur est calculé par PowerShell : on lit sa sortie, pas l'écho de ce qu'on tape
            ws.send_json({"t": "in", "d": "Write-Output ('fin-' + (6*7)); (Get-ChildItem -Name)\r"})
            seen = self.read_until(ws, "fin-42")
            self.read_until(ws, "témoin.txt") if "témoin.txt" not in seen else None
            ws.send_json({"t": "size", "rows": 40, "cols": 132})
            ws.send_json({"t": "in", "d": "$Host.UI.RawUI.WindowSize.Width\r"})
            self.read_until(ws, "132")
        session = terminal.get(self.cid)
        self.assertIsNotNone(session)  # la page fermée, le terminal tourne toujours
        self.assertEqual(session.size, (40, 132))
        with self.connect() as ws:  # rechargement de la page : ce qui était affiché revient
            self.assertIn("fin-42", self.read_until(ws, "fin-42"))
        self.assertIs(terminal.get(self.cid), session)  # le même terminal, pas un second

    def test_fermeture_tue_le_processus(self):
        with self.connect() as ws:
            self.read_until(ws, ">")  # l'invite
        session = terminal.get(self.cid)
        pid = session.pid
        resp = self.client.post("/api/terminal/close", json={"conversation_id": self.cid}, headers=ORIGIN)
        self.assertTrue(resp.json()["closed"])
        self.assertIsNone(terminal.get(self.cid))
        deadline = time.time() + 10
        while session.alive() and time.time() < deadline:
            time.sleep(0.2)
        self.assertFalse(session.alive(), pid)

    def test_supprimer_la_conversation_ferme_son_terminal(self):
        with self.connect() as ws:
            self.read_until(ws, ">")
        self.assertIsNotNone(terminal.get(self.cid))
        self.client.delete(f"/api/conversations/{self.cid}", headers=ORIGIN)
        self.assertIsNone(terminal.get(self.cid))

    def test_rattachement_au_job_par_numero(self):
        with self.connect() as ws:
            self.read_until(ws, ">")
        # Déjà rattaché à l'ouverture ; le refaire ne doit ni planter ni échouer
        self.assertTrue(jobs.attach_pid(terminal.get(self.cid).pid))
        self.assertFalse(jobs.attach_pid(0))


if __name__ == "__main__":
    unittest.main()
