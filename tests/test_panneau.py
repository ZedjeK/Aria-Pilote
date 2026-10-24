"""Panneau latéral, onglets Arrière-plan et Contexte : lecture sans voler l'agent, commandes oubliées, API."""

import asyncio
import json
import shutil
import sys
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from base import client, new_conversation, workspace

from aria_pilote import background, backups, conversations, permissions, tools
from aria_pilote.server import manager

HAS_POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")


@unittest.skipUnless(sys.platform == "win32" and HAS_POWERSHELL, "Windows et PowerShell nécessaires")
class BackgroundTab(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(background, "START_WAIT_S", 1.5)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = workspace()
        (self.root / "dit.py").write_text("import sys\nprint('\\x1b[32mvert\\x1b[0m bonjour', flush=True)\n"
                                          "import time\ntime.sleep(30)\n", encoding="utf-8")
        self.cid = new_conversation(self.root)
        backups.set_running(self.cid, backups.new_task_id())
        self.addCleanup(backups.set_running, self.cid, None)
        self.addCleanup(background.stop_conversation, self.cid)

    def start(self, command):
        tool = tools.TOOLS["run_command"]
        result = asyncio.run(tool.run(tool.prepare(str(self.root), {"command": command, "background": True}), self.cid))
        return result["output"].split(" :")[0]

    def test_le_panneau_ne_vole_rien_a_l_agent(self):
        bg = self.start("python dit.py")
        cmd = background.get(self.cid, bg)
        background.read(cmd)  # l'agent a lu ce qui est sorti au lancement
        time.sleep(0.5)
        self.assertIn("bonjour", background.peek(cmd))
        self.assertIn("bonjour", background.peek(cmd))  # relire ne consomme rien
        fresh, _, _ = background.read(cmd)
        self.assertEqual(fresh, "")  # et l'agent ne perd rien non plus : rien de nouveau pour lui

    def test_codes_couleur_retires(self):
        bg = self.start("python dit.py")
        out = background.peek(background.get(self.cid, bg))
        self.assertIn("vert bonjour", out)
        self.assertNotIn("\x1b[", out)
        self.assertNotIn("\r", out)  # fins de ligne Windows de PowerShell

    def test_commande_arretee_affichee_comme_telle(self):
        cmd = background.get(self.cid, self.start("python dit.py"))
        background.stop(cmd)
        self.assertEqual(cmd.state(), "arrêtée")  # et non « terminée (code de sortie 1) »

    def test_api_toutes_les_commandes_avec_sortie(self):
        bg = self.start("python dit.py")
        done = self.start("python -c \"print('fini')\"")
        api = client()
        items = api.get(f"/api/agent/background?conversation_id={self.cid}&all=true&output=true").json()
        self.assertEqual(items[0]["id"], bg)  # celles qui tournent d'abord
        self.assertTrue(items[0]["running"])
        finished = next(i for i in items if i["id"] == done)
        self.assertEqual((finished["running"], finished["returncode"]), (False, 0))
        self.assertIn("fini", finished["output"])
        # Sans « all », l'ancienne réponse (celles qui tournent) est inchangée
        self.assertEqual([c["id"] for c in api.get(f"/api/agent/background?conversation_id={self.cid}").json()], [bg])


class ForgetOld(unittest.TestCase):
    def test_seules_les_10_dernieres_terminees_sont_gardees(self):
        cid = "fffffffffff1"
        fake = []
        for i in range(13):
            cmd = background.Command(f"bgtest{i}", cid, None, f"echo {i}", False, proc=SimpleNamespace(pid=0))
            cmd.returncode, cmd.finished_at = 0, 1000 + i
            background._commands[cmd.id] = cmd
            fake.append(cmd.id)
        self.addCleanup(lambda: [background._commands.pop(i, None) for i in fake])
        background._forget_old(cid)
        kept = [c.id for c in background._commands.values() if c.cid == cid]
        self.assertEqual(len(kept), background.MAX_FINISHED)
        self.assertNotIn("bgtest0", kept)  # les plus anciennes sont oubliées
        self.assertIn("bgtest12", kept)


class ContextTab(unittest.TestCase):
    def setUp(self):
        self.root = workspace()
        self.api = client()

    def context(self, cid):
        return self.api.get(f"/api/agent/context?conversation_id={cid}").json()

    def test_sans_modele_ni_memoire(self):
        c = self.context(new_conversation(self.root))
        self.assertIsNone(c["model"])
        self.assertIsNone(c["memory"])
        self.assertTrue(c["agent"]["enabled"])
        self.assertEqual({s["name"] for s in c["skills"]} >= {"revue-code", "message-commit"}, True)
        self.assertIn("explorateur", {a["name"] for a in c["subagents"]})
        self.assertEqual(c["rules"], [])

    def test_modele_memoire_hooks_et_regles(self):
        (self.root / "ARIA.md").write_text("Tests : python -m unittest\n", encoding="utf-8")
        (self.root / ".aria").mkdir()
        (self.root / ".aria" / "hooks.json").write_text(json.dumps({"debut_tache": [{"commande": "git status"}]}),
                                                         encoding="utf-8")
        permissions.add(self.root, "git status*", "git status")
        cid = new_conversation(self.root, auto_write=True)
        conversations.update(cid, lambda c: c.update(task_id="t1", plan={"status": "pending", "task_id": "t1"}))
        model = SimpleNamespace(id="m", name="Qwen", key="q.gguf")
        settings = {"ctx_size": 32768, "cache_type": "q8_0", "gpu_layers": 99}
        with mock.patch.object(manager, "model", model), mock.patch.object(manager, "state", "ready"), \
                mock.patch.object(manager, "_settings", settings), mock.patch.object(manager, "engine", "llama.cpp"):
            c = self.context(cid)
        self.assertEqual((c["model"]["name"], c["model"]["ctx_size"], c["model"]["cache_type"]), ("Qwen", 32768, "q8_0"))
        self.assertEqual(c["memory"]["file"], "ARIA.md")
        self.assertEqual(c["agent"]["plan"], "pending")
        self.assertTrue(c["agent"]["auto_write"])
        self.assertEqual(c["hooks"]["project"], {"trusted": False, "error": None})  # pas encore approuvés
        self.assertEqual(c["rules"], ["git status*"])

    def test_occupation_et_resume(self):
        from aria_pilote import conversations
        cid = new_conversation(self.root)
        conversations.update(cid, lambda conv: conv.update(context={"used": 9000, "max": 32768},
                                                           summary={"upto": 12, "text": "résumé"}, squeeze=1))
        usage = self.context(cid)["usage"]
        self.assertEqual((usage["used"], usage["max"], usage["summarized"], usage["squeeze"]), (9000, 32768, 12, 1))

    def test_agent_desactive(self):
        c = self.context(new_conversation(self.root, enabled=False))
        self.assertFalse(c["agent"]["enabled"])
        self.assertIsNone(c["agent"]["workspace"])


if __name__ == "__main__":
    unittest.main()
