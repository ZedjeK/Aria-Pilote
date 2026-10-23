"""ask_user : l'agent pose une question, la tâche s'arrête, ta réponse la fait reprendre."""

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from base import client, events, new_conversation, workspace

from aria_pilote import agent, chat, conversations
from aria_pilote.server import manager


def run_ctx():
    return {"preset": {}, "model": "x", "plan": False, "allowed": None, "seen": {}, "stuck": False,
            "planned": False, "asked": False, "task_id": None}


async def ask(cid, root, args):
    ctx = run_ctx()
    call = {"id": "q1", "function": {"name": "ask_user", "arguments": json.dumps(args)}}
    out = events([e async for e in agent._execute(cid, str(root), call, ctx)])
    return ctx, out


class AskUserTool(unittest.TestCase):
    def setUp(self):
        self.root = workspace()
        self.cid = new_conversation(self.root)

    def test_question_en_attente_et_options_nettoyees(self):
        ctx, out = asyncio.run(ask(self.cid, self.root, {"question": "  SQLite ou PostgreSQL ? ",
                                                         "options": ["SQLite", "", 3, "PostgreSQL", "a", "b", "c"]}))
        result = next(e for e in out if e["type"] == "tool_result")
        self.assertEqual(result["status"], "pending")
        self.assertTrue(ctx["asked"])
        message = conversations.get(self.cid)["messages"][-1]
        self.assertEqual(message["args"], {"question": "SQLite ou PostgreSQL ?", "options": ["SQLite", "PostgreSQL", "a", "b"]})
        self.assertIsNotNone(agent.pending_question(conversations.get(self.cid)))

    def test_question_vide_refusee_sans_arreter_la_tache(self):
        ctx, out = asyncio.run(ask(self.cid, self.root, {"question": "  "}))
        self.assertEqual(next(e for e in out if e["type"] == "tool_result")["status"], "error")
        self.assertFalse(ctx["asked"])


class AskUserApi(unittest.TestCase):
    """Réponse par /api/chat : la réponse devient le résultat de l'outil, puis l'agent reprend."""

    def setUp(self):
        self.root = workspace()
        self.seen = {}

        async def fake_run(conv, preset, model_name):
            self.seen["runner"] = "agent"
            self.seen["messages"] = chat.build_messages(conversations.get(conv["id"]), "")
            yield chat.event({"type": "done", "message": None})

        async def fake_stream(conv, preset, model_name):
            self.seen["runner"] = "chat"
            yield chat.event({"type": "done", "message": None})

        for patcher in (mock.patch.object(agent, "run", fake_run), mock.patch.object(chat, "stream", fake_stream),
                        mock.patch.object(manager, "state", "ready"),
                        mock.patch.object(manager, "model", SimpleNamespace(id="t", name="Simu"))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.api = client()

    def asked(self, question="Quel champ ?", options=("a", "b"), enabled=True):
        cid = new_conversation(self.root, enabled=enabled)
        conversations.add_user_message(c := conversations.get(cid), "Ajoute un champ.")
        conversations.save(c)
        asyncio.run(ask(cid, self.root, {"question": question, "options": list(options)}))
        if not enabled:
            conversations.update(cid, lambda c: c["agent"].update(enabled=False))
        return cid

    def question(self, cid):
        return next(m for m in conversations.get(cid)["messages"] if m.get("name") == "ask_user")

    def test_reponse_enregistree_et_transmise_au_modele(self):
        cid = self.asked()
        self.api.post("/api/chat", json={"conversation_id": cid, "answer": "SQLite"})
        q = self.question(cid)
        self.assertEqual((q["status"], q["answer"]), ("ok", "SQLite"))
        self.assertIn({"role": "tool", "tool_call_id": "q1", "content": "Réponse de l'utilisateur : SQLite"},
                      self.seen["messages"])
        self.assertEqual(self.seen["runner"], "agent")
        self.assertEqual(sum(m["role"] == "user" for m in conversations.get(cid)["messages"]), 1)

    def test_deuxieme_reponse_refusee(self):
        cid = self.asked()
        self.api.post("/api/chat", json={"conversation_id": cid, "answer": "SQLite"})
        self.assertEqual(self.api.post("/api/chat", json={"conversation_id": cid, "answer": "x"}).status_code, 409)

    def test_decide_toi_meme(self):
        cid = self.asked()
        self.api.post("/api/chat", json={"conversation_id": cid, "answer": ""})
        q = self.question(cid)
        self.assertEqual((q["content"], q["answer"]), (agent.QUESTION_DECIDE, ""))

    def test_message_tape_vaut_reponse(self):
        cid = self.asked()
        self.api.post("/api/chat", json={"conversation_id": cid, "content": "8080 stp"})
        self.assertEqual(self.question(cid)["answer"], "8080 stp")
        self.assertEqual(sum(m["role"] == "user" for m in conversations.get(cid)["messages"]), 1)

    def test_agent_desactive_question_close(self):
        cid = self.asked(enabled=False)
        self.api.post("/api/chat", json={"conversation_id": cid, "content": "Parlons d'autre chose."})
        self.assertEqual(self.question(cid)["status"], "unanswered")
        self.assertEqual(self.seen["runner"], "chat")
        self.assertEqual(conversations.get(cid)["messages"][-1]["role"], "user")

    def test_message_vide_et_reponse_sans_question_refuses(self):
        cid = new_conversation(self.root)
        self.assertEqual(self.api.post("/api/chat", json={"conversation_id": cid, "content": "  "}).status_code, 400)
        self.assertEqual(self.api.post("/api/chat", json={"conversation_id": cid, "answer": "x"}).status_code, 409)


if __name__ == "__main__":
    unittest.main()
