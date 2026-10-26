"""Plan d'étapes affiché sous la réponse : rangé avec sa tâche, disponible aussi sans dossier."""
import asyncio
import unittest

from base import new_conversation  # en premier : Aria tourne dans un dossier temporaire

from aria_pilote import agent, conversations, tools


class PlanTest(unittest.TestCase):
    def test_plan_range_avec_sa_tache(self):
        cid = new_conversation()
        conversations.update(cid, lambda c: c.__setitem__("task_id", "t-42"))
        prepared = tools.prepare_update_todo(None, {"items": [
            {"content": "Lire la config", "status": "completed"},
            {"content": "Corriger le port", "status": "in_progress"},
            {"content": "Relancer", "status": "pending"},
        ]})
        result = asyncio.run(tools.run_update_todo(prepared, cid))
        self.assertEqual(result["todo"][1]["status"], "in_progress")  # envoyé à l'interface en direct
        todo = conversations.get(cid)["todo"]
        self.assertEqual(todo["task_id"], "t-42")
        self.assertEqual([i["content"] for i in todo["items"]], ["Lire la config", "Corriger le port", "Relancer"])

    def test_plan_sans_dossier(self):
        self.assertIn("update_todo", agent.allowed_tools(folder=False))
        prompt = agent._system_prompt({}, None, None)
        self.assertIn("update_todo (ton plan d'étapes", prompt)
        self.assertIn(agent.TODO_RULE, prompt)

    def test_etape_inconnue_ramenee_a_venir(self):
        prepared = tools.prepare_update_todo(None, {"items": [{"content": "Étape", "status": "bizarre"}, "Autre"]})
        self.assertEqual([i["status"] for i in prepared["items"]], ["pending", "pending"])


if __name__ == "__main__":
    unittest.main()
