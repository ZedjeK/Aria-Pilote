"""Contrôle du PC, étape 5C : actions ordinaires autorisées pour une tâche, limite d'actions réglable."""
import asyncio
import json
import unittest
from unittest import mock

from base import new_conversation  # en premier : Aria tourne dans un dossier temporaire

from aria_pilote import config, conversations, screen, toolrun, tools

WINDOW = {"id": 101, "titre": "Mail", "processus": "Mail.exe", "rect": (0, 0, 800, 600), "reduite": False, "proprietaire": None}
ELEMENTS = [
    {"n": 1, "type": "EditControl", "nom": "Objet", "rect": (10, 10, 400, 40), "valeur": "", "mot_de_passe": False, "actif": True},
    {"n": 2, "type": "ButtonControl", "nom": "Envoyer", "rect": (10, 500, 100, 540), "valeur": None, "mot_de_passe": False, "actif": True},
    {"n": 3, "type": "ButtonControl", "nom": "Gras", "rect": (200, 500, 240, 540), "valeur": None, "mot_de_passe": False, "actif": True},
]


class Base(unittest.TestCase):
    def setUp(self):
        self.cid = new_conversation()
        conversations.update(self.cid, lambda c: c.update({"screen_control": True, "task_id": "tache-1"}))
        self.calls = []
        fakes = {"windows": lambda: [WINDOW], "popups": lambda w: [], "read_elements": lambda w, extra=(): (ELEMENTS, [], False),
                 "foreground": lambda: 101, "owner_at": lambda x, y: 101, "bring_to_front": lambda h: True,
                 "focused_is_password": lambda: False, "click": lambda *a, **k: self.calls.append(("click", a)),
                 "press": lambda k: self.calls.append(("press", k)), "type_text": lambda t: self.calls.append(("type", t))}
        for name, fake in fakes.items():
            patcher = mock.patch.object(screen, name, side_effect=fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(screen, "SETTLE_S", 0)
        patcher.start()
        self.addCleanup(patcher.stop)
        tools._remember_screen(self.cid, WINDOW, ELEMENTS)

    def bind(self, **args):
        return tools.bind_screen_action(tools.prepare_screen_action(None, args), self.cid)

    def run_action(self, decision="allow", **args):
        """Comme l'agent : la carte d'accord (si elle vient) reçoit `decision` ; renvoie (accord demandé ?, résultat)."""
        async def scenario():
            out, asked = {}, []
            async for ev in toolrun.run_tool(self.cid, None, "agir_ecran", args, "c1", out):
                data = json.loads(ev.removeprefix("data: ").strip())
                if data["type"] == "approval":
                    asked.append(data)
                    toolrun.resolve_approval(data["approval_id"], decision)
            return asked, out["result"]
        asked, result = asyncio.run(scenario())
        # Le résultat est rangé comme le fait l'agent : il compte pour la limite et pour « même action que la précédente »
        conversations.update(self.cid, lambda c: c["messages"].append({
            "role": "tool", "name": "agir_ecran", "status": result["status"], "content": result["output"], "task_id": c["task_id"]}))
        return asked, result


class OrdinaryTest(Base):
    def test_raccourcis_a_risque(self):
        for spec in ("alt+f4", "ctrl+w", "win+r", "win", "suppr", "maj+suppr", "ctrl+alt+suppr", "ctrl+maj+echap"):
            self.assertTrue(tools._risky_keys(spec), spec)
        for spec in ("ctrl+s", "entree", "tab", "ctrl+c", "alt+tab"):
            self.assertFalse(tools._risky_keys(spec), spec)

    def test_ce_qui_reste_toujours_demande(self):
        self.assertTrue(self.bind(action="cliquer", element=3)["ordinary"])
        self.assertTrue(self.bind(action="taper", element=1, texte="Bonjour")["ordinary"])
        self.assertFalse(self.bind(action="cliquer", element=2)["ordinary"])  # « Envoyer » : bouton sensible
        self.assertFalse(self.bind(action="touches", touches="alt+f4")["ordinary"])


class TaskTest(Base):
    def test_autoriser_pour_la_tache_puis_redemander_ensuite(self):
        asked, _ = self.run_action("allow_task", action="cliquer", element=3)
        self.assertTrue(asked and asked[0]["ordinary"])
        self.assertEqual(conversations.get(self.cid)["screen_auto_task"], "tache-1")
        # Ordinaire, même tâche : plus de carte
        asked, result = self.run_action(action="taper", element=1, texte="Réunion")
        self.assertEqual(asked, [])
        self.assertEqual(result["rule"], "action ordinaire autorisée pour cette tâche")
        # Bouton sensible : toujours demandé
        asked, _ = self.run_action("deny", action="cliquer", element=2)
        self.assertEqual(len(asked), 1)
        self.assertFalse(asked[0]["ordinary"])
        # Nouvelle tâche : on redemande
        conversations.update(self.cid, lambda c: c.__setitem__("task_id", "tache-2"))
        asked, _ = self.run_action(action="cliquer", element=3)
        self.assertEqual(len(asked), 1)

    def test_reserve_aux_actions_a_l_ecran(self):
        async def scenario():
            future = asyncio.get_running_loop().create_future()
            toolrun._approvals["x"] = (future, self.cid, "command", None, "dir")
            try:
                with self.assertRaises(toolrun.ApprovalError):
                    toolrun.resolve_approval("x", "allow_task")
            finally:
                toolrun._approvals.pop("x", None)
        asyncio.run(scenario())


class LimitTest(Base):
    def test_limite_reglable(self):
        saved = config.get().get("screen_max_actions")
        config.update({"screen_max_actions": 20})
        self.addCleanup(config.update, {"screen_max_actions": saved})
        conversations.update(self.cid, lambda c: c["messages"].extend(
            {"role": "tool", "name": "agir_ecran", "status": "ok", "content": f"Fait : {i}", "task_id": "tache-1"} for i in range(20)))
        with self.assertRaises(tools.ToolError) as ctx:
            self.bind(action="cliquer", element=3)
        self.assertIn("20 actions", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
