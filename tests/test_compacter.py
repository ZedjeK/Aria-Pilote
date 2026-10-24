"""Compactage à la demande (bouton 🗜, « /compact ») et seuil du compactage automatique pour la jauge."""

import unittest
from types import SimpleNamespace
from unittest import mock

from base import ORIGIN, client, new_conversation

from aria_pilote import backups, context, conversations
from aria_pilote.server import manager


def fill(cid, turns):
    def add(conv):
        for i in range(turns):
            conv["messages"].append({"role": "user", "content": f"Question {i}"})
            conv["messages"].append({"role": "assistant", "content": f"Réponse {i}"})
    conversations.update(cid, add)


class Limit(unittest.TestCase):
    def test_seuil_selon_le_preset(self):
        self.assertEqual(context.limit({"max_tokens": 4096}, 32768), 32768 - 4096)
        self.assertEqual(context.limit({"max_tokens": 16384}, 32768), 32768 - 8192)  # réserve plafonnée au quart

    def test_la_jauge_recoit_le_seuil(self):
        cid = new_conversation()
        with mock.patch.object(manager, "_settings", {"ctx_size": 32768}), mock.patch.object(manager, "state", "ready"):
            usage = context.record_usage(cid, {"prompt_tokens": 9000, "completion_tokens": 1000}, {"max_tokens": 4096})
        self.assertEqual(usage, {"type": "context", "used": 10000, "max": 32768, "limit": 28672})
        self.assertEqual(conversations.get(cid)["context"]["limit"], 28672)


class CompactApi(unittest.TestCase):
    def setUp(self):
        self.api = client()
        self.seen = {}

        async def fake_summarize(conv, upto, cut, model_name, keep=None, instructions=None):
            self.seen.update(upto=upto, cut=cut, instructions=instructions)
            return "Résumé : l'utilisateur pose des questions numérotées."

        async def fake_count(messages, tools=None):
            return 100 * len(messages)

        for patcher in (mock.patch.object(context, "_summarize", fake_summarize),
                        mock.patch.object(context, "count_tokens", fake_count),
                        mock.patch.object(manager, "state", "ready"),
                        mock.patch.object(manager, "_settings", {"ctx_size": 32768}),
                        mock.patch.object(manager, "model", SimpleNamespace(id="t", name="Simu"))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def compact(self, cid, instructions=""):
        return self.api.post(f"/api/conversations/{cid}/compact", json={"instructions": instructions}, headers=ORIGIN)

    def test_compacter_avec_consigne(self):
        cid = new_conversation()
        fill(cid, 5)
        resp = self.compact(cid, "garde les décisions")
        self.assertEqual(resp.status_code, 200, resp.text)
        result = resp.json()
        self.assertEqual(result["type"], "compacted")
        self.assertEqual(self.seen["instructions"], "garde les décisions")
        conv = conversations.get(cid)
        self.assertEqual(conv["summary"]["upto"], 8)  # jusqu'à la dernière demande, gardée telle quelle
        self.assertEqual(len(conv["messages"]), 10)  # rien n'est effacé de l'historique
        self.assertLess(result["used"], result["before"])

    def test_rien_a_compacter(self):
        cid = new_conversation()
        fill(cid, 1)
        self.assertEqual(self.compact(cid).status_code, 409)

    def test_pas_pendant_une_reponse(self):
        cid = new_conversation()
        fill(cid, 3)
        backups.set_running(cid, backups.new_task_id())
        self.addCleanup(backups.set_running, cid, None)
        self.assertEqual(self.compact(cid).status_code, 409)

    def test_sans_modele(self):
        cid = new_conversation()
        fill(cid, 3)
        with mock.patch.object(manager, "state", "idle"):
            self.assertEqual(self.compact(cid).status_code, 409)


if __name__ == "__main__":
    unittest.main()
