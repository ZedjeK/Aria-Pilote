"""Page Réglages, étape B : comportement des agents, web et SearXNG, données."""

import copy
import json
import os
import unittest
from datetime import datetime, timedelta
from unittest import mock

from base import HOME, ORIGIN, client, new_conversation, sent_to_bin, workspace

from aria_pilote import agent, backups, config, conversations, main, searxng, subagents, tools, web


def keep_config(test):
    saved = copy.deepcopy(config.get())

    def restore():
        with config._lock:
            config._data = saved
            config._save_locked()
    test.addCleanup(restore)


class Behaviour(unittest.TestCase):
    def setUp(self):
        keep_config(self)
        self.api = client()
        self.root = workspace()

    def put(self, patch):
        return self.api.put("/api/config", json=patch, headers=ORIGIN)

    def test_bornes_et_validations(self):
        self.assertEqual(self.put({"max_tool_calls": 5}).status_code, 422)
        self.assertEqual(self.put({"web": {"results": 50}}).status_code, 422)
        self.assertEqual(self.put({"web": {"language": "klingon"}}).status_code, 400)
        self.assertEqual(self.put({"new_conversation": {"workspace": str(HOME / "nulle-part")}}).status_code, 400)
        # Agent d'office sans dossier : refusé
        self.assertEqual(self.put({"new_conversation": {"enabled": True}}).status_code, 400)
        cfg = self.put({"max_tool_calls": 40, "web": {"language": "fr"}}).json()
        self.assertEqual((cfg["max_tool_calls"], cfg["web"]["language"], cfg["web"]["results"]), (40, "fr", 8))

    def test_nouvelle_conversation_prend_les_reglages_de_depart(self):
        cfg = self.put({"new_conversation": {"workspace": str(self.root), "enabled": True, "plan_mode": True,
                                             "auto_web": True}}).json()
        self.assertEqual(cfg["new_conversation"]["workspace"], str(self.root.resolve()))
        conv = self.api.post("/api/conversations", json={}, headers=ORIGIN).json()
        self.assertEqual(conv["agent"], {"enabled": True, "workspace": str(self.root.resolve()), "plan_mode": True,
                                         "auto_web": True, "auto_write": False})  # jamais d'écriture d'office

    def test_dossier_par_defaut_disparu(self):
        config.update({"new_conversation": {"workspace": str(HOME / "efface"), "enabled": True}})
        conv = self.api.post("/api/conversations", json={}, headers=ORIGIN).json()
        self.assertNotIn("agent", conv)  # ignoré sans erreur : la conversation se crée quand même

    def test_reglages_explicites_du_chat(self):
        conv = self.api.post("/api/conversations", headers=ORIGIN,
                             json={"agent": {"enabled": True, "workspace": str(self.root), "plan_mode": False}}).json()
        self.assertTrue(conv["agent"]["enabled"])
        bad = self.api.post("/api/conversations", headers=ORIGIN,
                            json={"agent": {"enabled": True, "workspace": str(HOME / "nulle-part")}})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.api.post("/api/conversations", headers=ORIGIN, json={"agent": {"enabled": False}}).json()
                         .get("agent"), None)


class WebSwitch(unittest.TestCase):
    def setUp(self):
        keep_config(self)

    def test_web_coupe_retire_les_outils(self):
        config.update({"web": {"enabled": False}})
        allowed = agent.allowed_tools(plan_mode=False)
        self.assertNotIn("web_search", allowed)
        self.assertNotIn("web_fetch", allowed)
        self.assertIn("read_file", allowed)
        self.assertNotIn("web_search", agent.allowed_tools(plan_mode=True))
        self.assertNotIn("chercheur-web", subagents.usable())  # il n'a que des outils web
        self.assertIn("explorateur", subagents.usable())
        self.assertIn(agent.WEB_RULE_OFF, agent._system_prompt({}, str(HOME), None))

    def test_web_actif_par_defaut(self):
        self.assertIsNone(agent.allowed_tools(plan_mode=False))
        self.assertIn("chercheur-web", subagents.usable())
        self.assertIn("web_search et web_fetch", agent._system_prompt({}, str(HOME), None))
        self.assertEqual(tools.TOOLS["web_search"].kind, "web")

    def test_parametres_searxng(self):
        self.assertEqual(web.search_params("q"), {"q": "q", "format": "json", "safesearch": 0})
        config.update({"web": {"language": "fr", "safesearch": 2}})
        self.assertEqual(web.search_params("q"), {"q": "q", "format": "json", "safesearch": 2, "language": "fr"})


class SearxIdle(unittest.TestCase):
    def test_arret_apres_inactivite(self):
        manager = searxng.SearxManager()
        manager.proc = mock.Mock(poll=mock.Mock(return_value=None))  # « en marche »
        manager.last_used = 1000.0
        self.assertFalse(manager.idle_expired(0, now=99999))  # 0 : jamais
        self.assertFalse(manager.idle_expired(10, now=1000 + 9 * 60))
        self.assertTrue(manager.idle_expired(10, now=1000 + 11 * 60))
        manager.proc = None  # pas lancé par Aria : rien à arrêter
        self.assertFalse(manager.idle_expired(10, now=99999))


class Data(unittest.TestCase):
    def setUp(self):
        keep_config(self)
        self.api = client()

    def test_vue_d_ensemble_et_emplacements(self):
        new_conversation()
        d = self.api.get("/api/data").json()
        self.assertGreaterEqual(d["conversations"]["count"], 1)
        self.assertEqual(d["app"], str(config.ROOT))
        self.assertEqual({log["name"] for log in d["logs"]}, {"aria_pilote.log", "searxng.log"})
        # Une liste fermée : jamais un chemin venu de la page
        self.assertEqual(self.api.post("/api/data/open", json={"target": "C:\\Windows"}, headers=ORIGIN).status_code, 400)
        with mock.patch.object(os, "startfile", create=True) as start:
            self.assertEqual(self.api.post("/api/data/open", json={"target": "backups"}, headers=ORIGIN).status_code, 200)
        start.assert_called_once_with(backups.BACKUP_DIR)

    def test_conversations_inactives(self):
        old, fresh = new_conversation(), new_conversation()
        path = conversations._path(old)
        conv = json.loads(path.read_text(encoding="utf-8"))
        conv["updated_at"] = (datetime.now() - timedelta(days=40)).isoformat(timespec="seconds")
        path.write_text(json.dumps(conv), encoding="utf-8")
        self.assertEqual(conversations.inactive_since(0), [])
        gone = conversations.inactive_since(30)
        self.assertIn(old, gone)
        self.assertNotIn(fresh, gone)
        self.assertEqual(conversations.to_trash([old]), [old])
        self.assertIsNone(conversations.get(old))
        self.assertIn(f"{old}.json", sent_to_bin)  # à la corbeille, pas effacée

    def test_tout_a_la_corbeille(self):
        new_conversation()
        result = self.api.post("/api/data/conversations/trash", headers=ORIGIN).json()
        self.assertGreaterEqual(result["trashed"], 1)
        self.assertEqual(result["conversations"]["count"], 0)

    def test_pas_pendant_une_tache(self):
        cid = new_conversation()
        backups.set_running(cid, backups.new_task_id())
        self.addCleanup(backups.set_running, cid, None)
        self.assertEqual(self.api.post("/api/data/conversations/trash", headers=ORIGIN).status_code, 409)
        self.assertIsNotNone(conversations.get(cid))


if __name__ == "__main__":
    unittest.main()
