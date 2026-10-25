"""Agent unifié : le dossier attaché donne les outils de fichiers ; un plan en attente verrouille écritures et commandes."""

import asyncio
import copy
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from base import ORIGIN, client, events, new_conversation, workspace

from aria_pilote import agent, chat, config, conversations, subagents
from aria_pilote.server import manager


def run_ctx(task_id="t1"):
    return {"preset": {}, "model": "x", "allowed": None, "seen": {}, "stuck": False,
            "planned": False, "asked": False, "task_id": task_id}


def call(name, args, call_id="c1"):
    return {"id": call_id, "function": {"name": name, "arguments": json.dumps(args)}}


async def execute(cid, root, name, args, call_id="c1"):
    return events([e async for e in agent._execute(cid, str(root), call(name, args, call_id), run_ctx())])


def keep_config(test):
    saved = copy.deepcopy(config.get())

    def restore():
        with config._lock:
            config._data = saved
            config._save_locked()
    test.addCleanup(restore)


class Capabilities(unittest.TestCase):
    def test_sans_dossier_aucun_outil_de_fichiers(self):
        self.assertEqual(set(agent.allowed_tools(folder=False)), {"load_skill", "web_search", "web_fetch"})
        # avec un dossier : tous, sauf ceux de l'écran tant que le contrôle du PC n'est pas activé
        self.assertEqual(set(agent.allowed_tools(folder=True)),
                         {n for n, t in agent.tools.TOOLS.items() if not t.kind.startswith("screen")})
        self.assertIsNone(agent.allowed_tools(folder=True, screen=True))
        prompt = agent._system_prompt({}, None, None)
        self.assertIn("Tu n'as accès à aucun fichier", prompt)
        self.assertNotIn("Dossier de travail", prompt)
        self.assertIn(agent.PLAN_RULE, prompt)

    def test_sous_agents_selon_le_dossier(self):
        root = str(workspace())
        self.assertEqual(sorted(subagents.usable(None)), ["chercheur-web", "planificateur"])
        self.assertIn("explorateur", subagents.usable(root))
        # Sans dossier, le planificateur garde le web mais perd la lecture des fichiers
        self.assertEqual(subagents.usable(None)["planificateur"].tools, ["load_skill", "web_search", "web_fetch"])
        self.assertIn("read_file", subagents.usable(root)["planificateur"].tools)

    def test_dossier_attache_ou_pas(self):
        root = workspace()
        self.assertEqual(agent.attached_root(conversations.get(new_conversation(root))), str(root))
        self.assertIsNone(agent.attached_root(conversations.get(new_conversation(root, enabled=False))))
        self.assertIsNone(agent.attached_root(conversations.get(new_conversation())))


class PlanLock(unittest.TestCase):
    def setUp(self):
        self.root = workspace()
        self.cid = new_conversation(self.root)
        conversations.update(self.cid, lambda c: c.__setitem__("task_id", "t1"))

    def test_plan_propose_puis_ecriture_refusee(self):
        asyncio.run(execute(self.cid, self.root, "propose_plan", {"plan": "1. Créer note.txt"}))
        self.assertTrue(agent.plan_locked(conversations.get(self.cid)))
        out = asyncio.run(execute(self.cid, self.root, "write_file", {"path": "note.txt", "content": "x"}, "c2"))
        result = next(e for e in out if e["type"] == "tool_result")
        self.assertEqual(result["status"], "denied")
        self.assertIn("🔒", result["output"])
        self.assertFalse((self.root / "note.txt").exists())  # bloqué par le code, pas par une consigne
        # La lecture reste possible pendant que le plan attend
        (self.root / "lu.txt").write_text("contenu", encoding="utf-8")
        out = asyncio.run(execute(self.cid, self.root, "read_file", {"path": "lu.txt"}, "c3"))
        self.assertEqual(next(e for e in out if e["type"] == "tool_result")["status"], "ok")

    def test_le_verrou_ne_vaut_que_pour_la_tache(self):
        conversations.update(self.cid, lambda c: c.__setitem__("plan", {"status": "pending", "task_id": "t1"}))
        self.assertTrue(agent.plan_locked(conversations.get(self.cid)))
        conversations.update(self.cid, lambda c: c.__setitem__("task_id", "t2"))
        self.assertFalse(agent.plan_locked(conversations.get(self.cid)))


class PlanApi(unittest.TestCase):
    def setUp(self):
        keep_config(self)
        self.root = workspace()
        self.seen = {}

        async def fake_run(conv, preset, model_name):
            self.seen["conv"] = conversations.get(conv["id"])
            yield chat.event({"type": "done", "message": None})

        for patcher in (mock.patch.object(agent, "run", fake_run), mock.patch.object(manager, "state", "ready"),
                        mock.patch.object(manager, "model", SimpleNamespace(id="t", name="Simu"))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.api = client()

    def say(self, cid, text):
        return self.api.post("/api/chat", json={"conversation_id": cid, "content": text})

    def test_approuver_puis_abandonner(self):
        cid = new_conversation(self.root)
        conversations.update(cid, lambda c: c.update(task_id="t1", plan={"status": "pending", "task_id": "t1"}))
        conv = self.api.post("/api/agent/plan/approve", json={"conversation_id": cid}, headers=ORIGIN).json()
        self.assertEqual(conv["plan"]["status"], "approved")
        self.assertFalse(agent.plan_locked(conv))
        self.assertEqual(self.api.post("/api/agent/plan/approve", json={"conversation_id": cid},
                                       headers=ORIGIN).status_code, 409)  # plus rien à approuver
        conversations.update(cid, lambda c: c.__setitem__("plan", {"status": "pending", "task_id": "t1"}))
        conv = self.api.post("/api/agent/plan/cancel", json={"conversation_id": cid}, headers=ORIGIN).json()
        self.assertEqual(conv["plan"]["status"], "cancelled")

    def test_toujours_proposer_un_plan(self):
        config.update({"always_plan": True})
        cid = new_conversation(self.root)
        self.say(cid, "Refais le module de facturation.")
        conv = self.seen["conv"]
        self.assertEqual(conv["plan"], {"status": "required", "task_id": conv["task_id"]})
        # « Modifie le plan… » pendant que le plan attend : même tâche, le verrou reste
        self.say(cid, "Modifie le plan : garde l'ancien format.")
        self.assertEqual(self.seen["conv"]["task_id"], conv["task_id"])
        self.assertTrue(agent.plan_locked(self.seen["conv"]))

    def test_pas_de_plan_exige_sans_dossier(self):
        config.update({"always_plan": True})
        cid = new_conversation()
        self.say(cid, "Raconte-moi une histoire.")
        self.assertNotIn("plan", self.seen["conv"])

    def test_discussion_sans_dossier_passe_par_l_agent(self):
        cid = new_conversation()
        self.say(cid, "Bonjour !")
        self.assertIsNone(agent.attached_root(self.seen["conv"]))


if __name__ == "__main__":
    unittest.main()
