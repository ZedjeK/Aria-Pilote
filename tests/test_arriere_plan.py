"""Commandes en arrière-plan : vrais processus PowerShell / Python, lecture, attente, arrêt de tout l'arbre."""

import asyncio
import json
import shutil
import socket
import sys
import time
import unittest
from unittest import mock

from base import ORIGIN, SRC, client, new_conversation, workspace

from aria_pilote import agent, background, backups, conversations, tools

HAS_POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
TICKS = "import time\nprint('prêt')\nfor i in range(1, 100):\n    time.sleep(0.5)\n    print(f'tick {i}')\n"


def listening(port):
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


@unittest.skipUnless(sys.platform == "win32" and HAS_POWERSHELL, "Windows et PowerShell nécessaires")
class Background(unittest.TestCase):
    def setUp(self):
        # Attente au lancement réduite (3 s en vrai) : la suite reste rapide, le comportement vérifié est le même
        patcher = mock.patch.object(background, "START_WAIT_S", 1.5)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = workspace()
        (self.root / "ticks.py").write_text(TICKS, encoding="utf-8")
        shutil.copy(SRC / "bench/tasks/11-serveur-arriere-plan/fixture/serveur.py", self.root)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        (self.root / "serveur.json").write_text(json.dumps({"port": self.port, "code": "TEST-42"}), encoding="utf-8")
        self.cid = new_conversation(self.root)
        self.task = backups.new_task_id()
        backups.set_running(self.cid, self.task)
        self.addCleanup(backups.set_running, self.cid, None)
        self.addCleanup(background.stop_conversation, self.cid)

    def call(self, name, args):
        tool = tools.TOOLS[name]
        try:
            return asyncio.run(tool.run(tool.prepare(str(self.root), args), self.cid))
        except tools.ToolError as exc:
            return {"status": "error", "output": str(exc)}

    def start(self, command, **extra):
        result = self.call("run_command", {"command": command, "background": True, **extra})
        return result, result["output"].split(" :")[0]

    def test_lancement_rapide_et_premiere_ligne_lisible(self):
        start = time.time()
        result, _ = self.start("python ticks.py")
        self.assertLess(time.time() - start, 6)
        self.assertEqual(result["status"], "ok")
        self.assertIn("prêt", result["output"])  # accents intacts (PYTHONUTF8)

    def test_wait_for_et_lecture_incrementale(self):
        _, bg = self.start("python ticks.py")
        out = self.call("command_output", {"id": bg, "wait_for": "tick 6", "timeout": 10})["output"]
        self.assertIn("tick 6", out)
        self.assertNotIn("n'est pas apparu", out)
        out = self.call("command_output", {"id": bg})["output"]
        self.assertNotIn("prêt", out)
        out = self.call("command_output", {"id": bg, "wait_for": "jamais", "timeout": 1})["output"]
        self.assertIn("n'est pas apparu en 1 s", out)
        self.assertTrue(agent._bg_running(self.cid, {"id": bg}))  # exempté de la détection des boucles

    def test_serveur_interroge_puis_arrete_avec_ses_enfants(self):
        _, bg = self.start("python serveur.py")
        out = self.call("command_output", {"id": bg, "wait_for": "Serveur prêt", "timeout": 10})["output"]
        self.assertIn("Serveur prêt", out)
        self.assertTrue(listening(self.port))
        self.assertIn("Arrêtée", self.call("stop_command", {"id": bg})["output"])
        time.sleep(0.5)
        self.assertFalse(listening(self.port))  # pwsh → python : tout l'arbre est arrêté

    def test_limite_keep_et_fin_de_tache(self):
        self.start("python ticks.py")
        self.start("python ticks.py", keep=True)
        self.start("python ticks.py")
        result, _ = self.start("python ticks.py")
        self.assertEqual(result["status"], "error")
        self.assertIn("Déjà 3", result["output"])
        background.stop_task(self.cid, self.task)
        alive = background.running(self.cid)
        self.assertEqual(len(alive), 1)
        self.assertTrue(alive[0]["keep"])

    def test_api_barre_agent(self):
        _, bg = self.start("python ticks.py", keep=True)
        api = client()
        self.assertEqual([c["id"] for c in api.get(f"/api/agent/background?conversation_id={self.cid}").json()], [bg])
        after = api.post("/api/agent/background/stop", json={"conversation_id": self.cid, "id": bg}, headers=ORIGIN).json()
        self.assertEqual(after, [])
        self.assertEqual(api.post("/api/agent/background/stop", json={"conversation_id": self.cid, "id": "bg999"},
                                  headers=ORIGIN).status_code, 404)

    def test_plantage_immediat_et_cloisonnement(self):
        result, _ = self.start("python introuvable.py")
        self.assertEqual(result["status"], "error")
        self.assertIn("code de sortie", result["output"])
        _, bg = self.start("python ticks.py")
        other = new_conversation(self.root)
        tool = tools.TOOLS["command_output"]
        with self.assertRaises(tools.ToolError):
            asyncio.run(tool.run(tool.prepare(str(self.root), {"id": bg}), other))

    def test_apercu_de_l_accord(self):
        prep = tools.TOOLS["run_command"].prepare(str(self.root), {"command": "python serveur.py", "background": True, "keep": True})
        self.assertIn("en arrière-plan", prep["preview"])
        self.assertIn("gardée", prep["preview"])
        prep = tools.TOOLS["run_command"].prepare(str(self.root), {"command": "python serveur.py", "keep": True})
        self.assertFalse(prep["keep"])  # keep sans background : ignoré

    def test_suppression_de_la_conversation_arrete_ses_commandes(self):
        self.start("python ticks.py", keep=True)
        client().delete(f"/api/conversations/{self.cid}", headers=ORIGIN)
        self.assertEqual(background.running(self.cid), [])
        self.assertIsNone(conversations.get(self.cid))


if __name__ == "__main__":
    unittest.main()
