"""Chantier 4 : observer l'écran (4B : fenêtres, éléments numérotés, captures) et y agir (4C)."""
import asyncio
import json
import unittest
from unittest import mock

from PIL import Image

from base import ORIGIN, client, new_conversation  # en premier : Aria tourne dans un dossier temporaire

from aria_pilote import agent, chat, config, conversations, screen, toolrun, tools
from aria_pilote.server import ServerManager

WINDOWS = [
    {"id": 101, "titre": "Sans titre - Bloc-notes", "processus": "Notepad.exe", "rect": (100, 100, 900, 700), "reduite": False},
    {"id": 202, "titre": "Calculatrice", "processus": "CalculatorApp.exe", "rect": (0, 0, 400, 600), "reduite": True},
]
ELEMENTS = [
    {"n": 1, "type": "EditControl", "nom": "Nom du fichier", "rect": (150, 150, 450, 180), "valeur": "rapport.xlsx",
     "mot_de_passe": False, "actif": True},
    {"n": 2, "type": "EditControl", "nom": "Mot de passe", "rect": (150, 200, 450, 230), "valeur": None,
     "mot_de_passe": True, "actif": True},
    {"n": 3, "type": "ButtonControl", "nom": "Exporter", "rect": (150, 600, 250, 640), "valeur": None,
     "mot_de_passe": False, "actif": False},
]


def setting(test, **values):
    saved = {k: config.get().get(k) for k in values}
    config.update(values)
    test.addCleanup(config.update, saved)


def vision(test, on):
    patcher = mock.patch.object(ServerManager, "vision", new_callable=mock.PropertyMock, return_value=on)
    patcher.start()
    test.addCleanup(patcher.stop)


class DescriptionTest(unittest.TestCase):
    def test_trouver_une_fenetre(self):
        self.assertEqual(screen.find("101", WINDOWS)["id"], 101)
        self.assertEqual(screen.find("bloc-notes", WINDOWS)["id"], 101)
        self.assertEqual(screen.find("calculatorapp", WINDOWS)["id"], 202)  # par programme
        with self.assertRaises(screen.ScreenError) as ctx:
            screen.find("Photoshop", WINDOWS)
        self.assertIn("Calculatrice (202)", str(ctx.exception))

    def test_liste_des_fenetres(self):
        text = screen.describe_windows(WINDOWS)
        self.assertIn("[101] Sans titre - Bloc-notes — Notepad.exe", text)
        self.assertIn("Calculatrice — CalculatorApp.exe (réduite)", text)

    def test_elements_et_mot_de_passe(self):
        text = screen.describe_elements(WINDOWS[0], ELEMENTS, ["Erreur 0x80070005"], truncated=True)
        self.assertIn("[1] champ « Nom du fichier » = « rapport.xlsx »", text)
        self.assertIn("[2] champ « Mot de passe » (champ mot de passe", text)
        self.assertIn("[3] bouton « Exporter » (désactivé)", text)
        self.assertIn("liste coupée", text)
        self.assertIn("« Erreur 0x80070005 »", text)

    def test_reperes_a_la_bonne_place(self):
        image = Image.new("RGB", (800, 600), (255, 255, 255))
        small, scale = screen.fit(image, 400)
        self.assertEqual((small.size, scale), ((400, 300), 0.5))
        marked = screen.draw_marks(small, ELEMENTS[:1], (100, 100), scale)
        # Cadre du champ 1 : (150-100)*0.5 = 25 à gauche, (180-100)*0.5 = 40 en bas
        self.assertNotEqual(marked.getpixel((25, 35)), (255, 255, 255))
        self.assertEqual(marked.getpixel((100, 35)), (255, 255, 255))  # intérieur du cadre laissé intact

    def test_fenetre_reduite_refusee(self):
        with self.assertRaises(screen.ScreenError):
            screen.capture_window(WINDOWS[1])


class ActivationTest(unittest.TestCase):
    def test_coupe_par_defaut(self):
        self.assertFalse(config.get().get("screen_control"))
        self.assertFalse(agent.screen_enabled({"screen_control": True}))
        self.assertNotIn("voir_ecran", agent.allowed_tools(folder=False))
        self.assertNotIn("Écran du PC", agent._system_prompt({"screen_control": True}, None, None))

    def test_active_dans_les_reglages_puis_la_conversation(self):
        setting(self, screen_control=True)
        self.assertFalse(agent.screen_enabled({}))
        conv = {"screen_control": True}
        self.assertTrue(agent.screen_enabled(conv))
        self.assertIn("voir_ecran", agent.allowed_tools(folder=False, screen=True))
        self.assertIn("lire_fenetre", agent.allowed_tools(folder=True, screen=True) or tools.TOOLS)
        self.assertIn("Écran du PC", agent._system_prompt(conv, None, None))

    def test_conversation_refusee_si_coupe_dans_les_reglages(self):
        cid = new_conversation()
        with client() as c:
            self.assertEqual(c.patch(f"/api/conversations/{cid}", json={"screen_control": True}, headers=ORIGIN).status_code, 409)
            setting(self, screen_control=True)
            self.assertEqual(c.patch(f"/api/conversations/{cid}", json={"screen_control": True}, headers=ORIGIN).status_code, 200)
        self.assertTrue(conversations.get(cid)["screen_control"])


class CaptureTest(unittest.TestCase):
    def fake_screen(self):
        for name, value in (("windows", lambda: WINDOWS), ("read_elements", lambda w: (ELEMENTS, ["Erreur"], False)),
                            ("capture_window", lambda w: (Image.new("RGB", (800, 600), "white"), (100, 100)))):
            patcher = mock.patch.object(screen, name, side_effect=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_capture_jointe_et_etat_memorise(self):
        self.fake_screen()
        vision(self, True)
        cid = new_conversation()
        result = tools._see_screen("Bloc-notes", cid)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["images"]), 1)
        self.assertIn("La capture est jointe", result["output"])
        state = conversations.get(cid)["screen_state"]
        self.assertEqual((state["window"], state["elements"]["1"]["nom"]), (101, "Nom du fichier"))
        self.assertEqual(state["capture"]["scale"], 1.0)

    def test_sans_vision_la_liste_seulement(self):
        self.fake_screen()
        vision(self, False)
        result = tools._see_screen("Bloc-notes", new_conversation())
        self.assertNotIn("images", result)
        self.assertIn("ne voit pas les images", result["output"])

    def test_capture_apres_le_lot_d_appels(self):
        """L'image arrive dans un message après le dernier résultat du lot, et seules les 2 dernières restent."""
        vision(self, True)
        cid = new_conversation()
        shot = {"file": "a" * 32 + ".png", "w": 64, "h": 64}

        def call(i, name):
            return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": "{}"}}

        messages = [{"role": "user", "content": "Regarde"}]
        for turn in range(3):
            messages += [
                {"role": "assistant", "content": "", "tool_calls": [call(f"{turn}a", "voir_ecran"), call(f"{turn}b", "lire_fenetre")]},
                {"role": "tool", "tool_call_id": f"c{turn}a", "name": "voir_ecran", "content": "capture", "images": [shot]},
                {"role": "tool", "tool_call_id": f"c{turn}b", "name": "lire_fenetre", "content": "liste"},
            ]
        conv = {**conversations.get(cid), "messages": messages}
        with mock.patch.object(chat.images, "data_url", return_value="data:image/png;base64,AAAA"):
            built = chat.build_messages(conv, None)
        roles = [m["role"] for m in built]
        self.assertEqual(roles, ["user", "assistant", "tool", "tool", "assistant", "tool", "tool", "user",
                                 "assistant", "tool", "tool", "user"])
        self.assertEqual(built[7]["content"][0]["type"], "image_url")


class ActionTest(unittest.TestCase):
    """agir_ecran : cible prise dans la dernière observation, accord à chaque action, refus en cas de doute."""

    def setUp(self):
        self.cid = new_conversation()
        self.calls = []
        fakes = {
            "windows": lambda: WINDOWS, "read_elements": lambda w: (ELEMENTS, [], False),
            "bring_to_front": lambda h: True, "owner_at": lambda x, y: 101, "focused_is_password": lambda: False,
            "click": lambda *a, **k: self.calls.append(("click", a, k)), "type_text": lambda t: self.calls.append(("type", t)),
            "press": lambda k: self.calls.append(("press", k)), "scroll": lambda *a: self.calls.append(("scroll", a)),
            "drag": lambda *a: self.calls.append(("drag", a)),
        }
        for name, fake in fakes.items():
            patcher = mock.patch.object(screen, name, side_effect=fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(screen, "SETTLE_S", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def observe(self, capture=None):
        tools._remember_screen(self.cid, WINDOWS[0], ELEMENTS, capture)

    def bind(self, **args):
        return tools.bind_screen_action(tools.prepare_screen_action(None, args), self.cid)

    def test_demandes_mal_formees(self):
        for args in ({"action": "sauter"}, {"action": "cliquer"}, {"action": "taper"},
                     {"action": "touches", "touches": "ctrl+licorne"}, {"action": "defiler", "direction": "dedans"},
                     {"action": "glisser", "element": 1}):
            with self.assertRaises(tools.ToolError, msg=args):
                tools.prepare_screen_action(None, args)

    def test_rien_sans_observation(self):
        with self.assertRaises(tools.ToolError) as ctx:
            self.bind(action="cliquer", element=1)
        self.assertIn("Observe d'abord", str(ctx.exception))

    def test_apercu_et_point_vise(self):
        self.observe()
        p = self.bind(action="cliquer", element=1)
        self.assertEqual(p["point"], (300, 165))  # centre du champ 1
        self.assertIn("Cliquer sur [1] champ « Nom du fichier »", p["preview"])
        self.assertIn("Sans titre - Bloc-notes", p["preview"])

    def test_coordonnees_remises_a_l_echelle(self):
        with self.assertRaises(tools.ToolError):  # sans capture, pas de coordonnées
            self.observe()
            self.bind(action="cliquer", x=10, y=10)
        self.observe({"origin": [100, 100], "scale": 0.5, "size": [400, 300]})
        self.assertEqual(self.bind(action="cliquer", x=100, y=50)["point"], (300, 200))
        with self.assertRaises(tools.ToolError):
            self.bind(action="cliquer", x=399, y=299.9 * 3)  # hors de la fenêtre

    def test_refus(self):
        self.observe()
        with self.assertRaises(tools.ToolError) as ctx:
            self.bind(action="taper", element=2, texte="secret")
        self.assertIn("mot de passe", str(ctx.exception))
        with self.assertRaises(tools.ToolError):
            self.bind(action="cliquer", element=9)  # numéro inconnu
        moved = [{**WINDOWS[0], "rect": (120, 100, 920, 700)}]
        with mock.patch.object(screen, "windows", side_effect=lambda: moved):
            with self.assertRaises(tools.ToolError) as ctx:
                self.bind(action="cliquer", element=1)
        self.assertIn("a bougé", str(ctx.exception))
        own = [{**WINDOWS[0], "titre": "Aria Pilote - Opera"}]
        with mock.patch.object(screen, "windows", side_effect=lambda: own):
            with self.assertRaises(tools.ToolError) as ctx:
                self.bind(action="cliquer", element=1)
        self.assertIn("Aria Pilote", str(ctx.exception))

    def test_action_puis_nouvelle_observation(self):
        self.observe()
        result = tools._act(self.bind(action="taper", element=1, texte="bilan.xlsx"), self.cid)
        self.assertEqual(self.calls, [("click", (300, 165), {}), ("type", "bilan.xlsx")])
        self.assertTrue(result["output"].startswith("Fait : Taper « bilan.xlsx » dans [1] champ"))
        self.assertIn("Après l'action", result["output"])

    def test_repetition_comptee(self):
        """La même action refaite dans la tâche est dite au modèle : il ne peut plus prétendre l'avoir faite une fois."""
        self.observe()
        first = tools._act(self.bind(action="cliquer", element=1), self.cid)
        self.assertNotIn("fois dans cette tâche", first["output"])
        conversations.append_message(self.cid, {"role": "tool", "name": "agir_ecran", "content": first["output"],
                                                "status": "ok", "tool_call_id": "c1"})
        second = tools._act(self.bind(action="cliquer", element=1), self.cid)
        self.assertIn("(2e fois dans cette tâche)", second["output"])
        self.assertIn("rends compte à l'utilisateur sans autre action", second["output"])

    def test_cible_recouverte(self):
        self.observe()
        p = self.bind(action="cliquer", element=3)
        with mock.patch.object(screen, "owner_at", side_effect=lambda x, y: 999):
            with self.assertRaises(tools.ToolError) as ctx:
                tools._act(p, self.cid)
        self.assertIn("recouvre", str(ctx.exception))
        self.assertEqual(self.calls, [])

    def test_mot_de_passe_sous_le_curseur(self):
        self.observe()
        p = self.bind(action="taper", texte="hunter2")
        with mock.patch.object(screen, "focused_is_password", side_effect=lambda: True):
            with self.assertRaises(tools.ToolError):
                tools._act(p, self.cid)
        self.assertEqual(self.calls, [])

    def test_accord_a_chaque_action_et_verrou_du_plan(self):
        self.assertIsNone(toolrun.APPROVAL_FLAGS["screen_action"])
        self.assertIn("screen_action", agent.LOCKED_KINDS)
        self.observe()

        async def scenario():
            out, seen = {}, []
            async for ev in toolrun.run_tool(self.cid, None, "agir_ecran", {"action": "cliquer", "element": 1}, "c1", out):
                data = json.loads(ev.removeprefix("data: ").strip())
                seen.append(data)
                if data["type"] == "approval":
                    toolrun.resolve_approval(data["approval_id"], "deny")
            return seen, out["result"]

        seen, result = asyncio.run(scenario())
        self.assertEqual(seen[0]["kind"], "screen_action")
        self.assertIn("Cliquer sur [1]", seen[0]["preview"])
        self.assertEqual(result["status"], "denied")
        self.assertEqual(self.calls, [])  # refusé : rien n'a bougé


class GuardTest(unittest.TestCase):
    """Garde-fous de l'étape 4D : applis interdites, liste autorisée, bouton sensible, limite d'actions."""

    DENIED = [
        {"id": 1, "titre": "Paramètres", "processus": "SystemSettings.exe", "rect": (0, 0, 10, 10), "reduite": False},
        {"id": 2, "titre": "Windows PowerShell", "processus": "powershell.exe", "rect": (0, 0, 10, 10), "reduite": False},
        {"id": 3, "titre": "Aria Pilote", "processus": "msedgewebview2.exe", "rect": (0, 0, 10, 10), "reduite": False},
    ]

    def test_applis_interdites(self):
        for w in self.DENIED:
            self.assertTrue(screen.is_denied(w), w["titre"])
        self.assertFalse(screen.is_denied(WINDOWS[0]))  # Bloc-notes

    def test_liste_autorisee(self):
        listed = WINDOWS + self.DENIED
        self.assertEqual([w["id"] for w in screen.allowed_windows(listed, None)], [101, 202])  # interdites retirées
        allow = ["notepad.exe"]
        self.assertEqual([w["id"] for w in screen.allowed_windows(listed, allow)], [101])
        self.assertEqual(screen.allowed_windows(listed, []), [])  # rien coché = rien
        screen.ensure_allowed(WINDOWS[0], ["notepad.exe"])  # ne lève pas
        with self.assertRaises(screen.ScreenError):
            screen.ensure_allowed(WINDOWS[0], ["calculatorapp.exe"])
        with self.assertRaises(screen.ScreenError):
            screen.ensure_allowed(self.DENIED[0], None)

    def test_bouton_sensible_et_repetition(self):
        self.assertTrue(tools._is_sensitive("Envoyer le message"))
        self.assertTrue(tools._is_sensitive("Supprimer définitivement"))
        self.assertFalse(tools._is_sensitive("Annuler"))
        cid = new_conversation()
        sensitive = [{"n": 1, "type": "ButtonControl", "nom": "Envoyer", "rect": (10, 10, 60, 40),
                      "valeur": None, "mot_de_passe": False, "actif": True}]
        tools._remember_screen(cid, WINDOWS[0], sensitive)
        with mock.patch.object(screen, "windows", side_effect=lambda: WINDOWS):
            p = tools.bind_screen_action(tools.prepare_screen_action(None, {"action": "cliquer", "element": 1}), cid)
        self.assertIn("⚠", p["preview"])
        self.assertIn("difficile à défaire", p["preview"])

    def test_limite_d_actions(self):
        cid = new_conversation()
        conv = conversations.get(cid)
        conv["messages"] = [{"role": "tool", "name": "agir_ecran", "status": "ok", "task_id": conv.get("task_id"),
                             "content": "Fait : x."} for _ in range(tools.MAX_SCREEN_ACTIONS)]
        conversations.save(conv)
        tools._remember_screen(cid, WINDOWS[0], ELEMENTS)
        with mock.patch.object(screen, "windows", side_effect=lambda: WINDOWS):
            with self.assertRaises(tools.ToolError) as ctx:
                tools.bind_screen_action(tools.prepare_screen_action(None, {"action": "cliquer", "element": 1}), cid)
        self.assertIn(str(tools.MAX_SCREEN_ACTIONS), str(ctx.exception))

    def test_fenetre_hors_liste_refusee_a_l_action(self):
        cid = new_conversation()
        conversations.update(cid, lambda c: c.__setitem__("screen_allow", ["calculatorapp.exe"]))
        tools._remember_screen(cid, WINDOWS[0], ELEMENTS)  # Bloc-notes, pas dans la liste
        with mock.patch.object(screen, "windows", side_effect=lambda: WINDOWS):
            with self.assertRaises(tools.ToolError) as ctx:
                tools.bind_screen_action(tools.prepare_screen_action(None, {"action": "cliquer", "element": 1}), cid)
        self.assertIn("autorisées", str(ctx.exception))


class ScreenApiTest(unittest.TestCase):
    def test_liste_fenetres_coupee_sans_reglage(self):
        with client() as c:
            self.assertEqual(c.get("/api/screen/windows").status_code, 409)

    def test_liste_fenetres_par_programme(self):
        setting(self, screen_control=True)
        listed = WINDOWS + GuardTest.DENIED
        with mock.patch.object(screen, "windows", side_effect=lambda: listed):
            with client() as c:
                data = c.get("/api/screen/windows").json()
        procs = [row["processus"] for row in data]
        self.assertIn("notepad.exe", procs)
        self.assertNotIn("systemsettings.exe", procs)  # interdite, jamais listée

    def test_allow_list_enregistree_et_rendue(self):
        cid = new_conversation()
        with client() as c:
            setting(self, screen_control=True)
            c.patch(f"/api/conversations/{cid}", json={"screen_control": True, "screen_allow": ["Notepad.exe", " "]},
                    headers=ORIGIN)
            self.assertEqual(conversations.get(cid)["screen_allow"], ["notepad.exe"])  # nettoyée, en minuscules
            c.patch(f"/api/conversations/{cid}", json={"screen_control": False}, headers=ORIGIN)
        conv = conversations.get(cid)
        self.assertFalse(conv["screen_control"])
        self.assertNotIn("screen_allow", conv)  # la main rendue efface la liste


if __name__ == "__main__":
    unittest.main()
