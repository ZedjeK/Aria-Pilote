"""« 🗺 Plan d'abord » et « /plan » : le verrou du plan posé pour une seule demande, avec ou sans dossier."""
import unittest
from unittest import mock

from base import ORIGIN, client, new_conversation, workspace  # en premier : Aria tourne dans un dossier temporaire

from aria_pilote import agent, conversations
from aria_pilote.server import manager


class PlanFirstTest(unittest.TestCase):
    def setUp(self):
        self.http = client()
        for name, value in [("state", "ready"), ("model", mock.Mock(name="modele"))]:
            patcher = mock.patch.object(manager, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

        async def no_run(*args, **kwargs):
            yield "data: {}\n\n"
        patcher = mock.patch.object(agent, "run", no_run)
        patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, cid, **body):
        r = self.http.post("/api/chat", json={"conversation_id": cid, **body}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        return conversations.get(cid)

    def test_plan_exige_pour_cette_demande_dans_un_dossier(self):
        cid = new_conversation(workspace())
        conv = self.post(cid, content="Migre le projet", plan_first=True)
        self.assertEqual(conv["plan"], {"status": "required", "task_id": conv["task_id"]})
        self.assertTrue(agent.plan_locked(conv))

    def test_aussi_sans_dossier(self):
        cid = new_conversation()
        conv = self.post(cid, content="Organise mon déménagement", plan_first=True)
        self.assertTrue(agent.plan_locked(conv))

    def test_sans_la_puce_pas_de_verrou(self):
        cid = new_conversation(workspace())
        conv = self.post(cid, content="Corrige la faute")
        self.assertFalse(agent.plan_locked(conv))

    def test_la_demande_suivante_n_est_plus_verrouillee(self):
        cid = new_conversation(workspace())
        conv = self.post(cid, content="Refonds tout", plan_first=True)
        conversations.update(cid, lambda c: c.__setitem__("plan", {"status": "approved", "task_id": c["task_id"]}))
        conv = self.post(cid, content="Maintenant corrige la faute")
        self.assertFalse(agent.plan_locked(conv))  # nouvelle tâche, sans la puce : pas de plan exigé


if __name__ == "__main__":
    unittest.main()
