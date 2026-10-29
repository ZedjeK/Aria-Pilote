"""Contrôle du PC, étape 5B : le mode « PC entier » (bureau, barre des tâches, ouvrir une appli), garde-fous compris."""
import unittest
from unittest import mock

from PIL import Image

from base import ORIGIN, client, new_conversation  # en premier : Aria tourne dans un dossier temporaire

from aria_pilote import config, conversations, screen, tools
from aria_pilote.server import ServerManager

NOTEPAD = {"id": 101, "titre": "Sans titre - Bloc-notes", "processus": "Notepad.exe", "rect": (100, 100, 900, 700),
           "reduite": False, "proprietaire": None}
SETTINGS = {"id": 303, "titre": "Paramètres", "processus": "SystemSettings.exe", "rect": (0, 0, 400, 600),
            "reduite": False, "proprietaire": None}
BAR = {"id": 900, "titre": "Barre des tâches", "processus": "explorer.exe", "rect": (0, 1040, 1920, 1080),
       "reduite": False, "proprietaire": None}
ITEMS = {
    101: [{"type": "DocumentControl", "nom": "Texte", "rect": (110, 150, 890, 690)}],
    900: [{"type": "ButtonControl", "nom": "Démarrer", "rect": (10, 1045, 50, 1075)}],
}


def read(window, limit=None, extra=()):
    elements = []
    for source in [*extra, window]:
        for item in ITEMS.get(source["id"], []):
            elements.append({**item, "n": len(elements) + 1, "valeur": None, "mot_de_passe": False, "actif": True})
    return elements, [], False


class Base(unittest.TestCase):
    def setUp(self):
        self.cid = new_conversation()
        conversations.update(self.cid, lambda c: c.update({"screen_control": True, "screen_mode": "pc"}))
        self.calls = []
        self.front = NOTEPAD
        self.under = NOTEPAD
        fakes = {
            "windows": lambda: [NOTEPAD, SETTINGS], "popups": lambda w: [], "read_elements": read,
            "foreground": lambda: self.front["id"], "info": lambda h: {101: NOTEPAD, 303: SETTINGS, 900: BAR}.get(h),
            "taskbar": lambda: BAR, "window_at": lambda x, y: self.under, "desktop_rect": lambda: (0, 0, 1920, 1080),
            "capture_screen": lambda: (Image.new("RGB", (1920, 1080), "white"), (0, 0)),
            "focused_is_password": lambda: False, "open_app": lambda name: self.calls.append(("open", name)),
            "click": lambda *a, **k: self.calls.append(("click", a)), "press": lambda k: self.calls.append(("press", k)),
            "type_text": lambda t: self.calls.append(("type", t)),
        }
        for name, fake in fakes.items():
            patcher = mock.patch.object(screen, name, side_effect=fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(screen, "SETTLE_S", 0)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(tools.time, "sleep", lambda s: None)  # « ouvrir » attend 2 s : pas en test
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(ServerManager, "vision", new_callable=mock.PropertyMock, return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def bind(self, **args):
        return tools.bind_screen_action(tools.prepare_screen_action(None, args), self.cid)


class DesktopTest(Base):
    def test_tout_l_ecran_avec_premier_plan_et_barre_des_taches(self):
        result = tools._see_screen("", self.cid)
        self.assertIn("Au premier plan : « Sans titre - Bloc-notes »", result["output"])
        self.assertIn("« Démarrer »", result["output"])
        self.assertEqual(len(result["images"]), 1)
        state = conversations.get(self.cid)["screen_state"]
        self.assertTrue(state["desktop"])
        self.assertEqual(state["zone"], [0, 0, 1920, 1080])

    def test_appli_interdite_au_premier_plan_ni_lue_ni_numerotee(self):
        self.front = SETTINGS
        result = tools._see_screen("", self.cid)
        self.assertIn("une appli que je ne touche jamais", result["output"])
        self.assertNotIn("Texte", result["output"])

    def test_lire_sans_argument_en_mode_pc(self):
        result = tools._read_window("", self.cid)
        self.assertIn("Tout le PC", result["output"])

    def test_cliquer_sur_le_bouton_demarrer(self):
        tools._see_screen("", self.cid)
        self.under = BAR
        p = self.bind(action="cliquer", element=2)
        self.assertIn("Fenêtre visée : « Barre des tâches » (explorer.exe)", p["preview"])
        tools._act(p, self.cid)
        self.assertIn(("click", (30.0, 1060.0)), [(c[0], c[1][:2]) for c in self.calls])

    def test_cible_interdite_refusee_avant_et_au_moment_d_agir(self):
        tools._see_screen("", self.cid)
        self.under = SETTINGS
        with self.assertRaises(tools.ToolError):
            self.bind(action="cliquer", x=100, y=100)
        self.under = NOTEPAD
        p = self.bind(action="cliquer", x=500, y=400)
        self.under = SETTINGS  # une appli interdite est passée devant pendant ta décision
        with self.assertRaises(tools.ToolError):
            tools._act(p, self.cid)
        self.assertFalse([c for c in self.calls if c[0] == "click"])

    def test_touches_vers_le_premier_plan_controle(self):
        tools._see_screen("", self.cid)
        self.front = SETTINGS
        with self.assertRaises(tools.ToolError):
            self.bind(action="touches", touches="entree")

    def test_bureau_observe_puis_mode_fenetres_refuse(self):
        tools._see_screen("", self.cid)
        conversations.update(self.cid, lambda c: c.update({"screen_mode": "fenetres", "screen_allow": ["notepad.exe"]}))
        with self.assertRaises(tools.ToolError):
            self.bind(action="cliquer", element=1)


class OpenTest(Base):
    def test_ouvrir_une_appli(self):
        p = self.bind(action="ouvrir", appli="Calculatrice")
        self.assertTrue(p["preview"].startswith("Ouvrir l'appli « Calculatrice »"))
        result = tools._act(p, self.cid)
        self.assertIn(("open", "Calculatrice"), self.calls)
        self.assertIn("Vérifie que c'est bien la bonne appli", result["output"])

    def test_refus(self):
        for name in ("PowerShell", "Invite de commande", "Paramètres Windows", "regedit"):
            with self.subTest(name=name), self.assertRaises(tools.ToolError):
                tools.prepare_screen_action(None, {"action": "ouvrir", "appli": name})
        conversations.update(self.cid, lambda c: c.update({"screen_mode": "fenetres"}))
        with self.assertRaises(tools.ToolError):
            self.bind(action="ouvrir", appli="Calculatrice")  # seulement en mode PC entier


class ApiTest(unittest.TestCase):
    def setUp(self):
        saved = config.get().get("screen_control")
        config.update({"screen_control": True})
        self.addCleanup(config.update, {"screen_control": saved})
        self.http = client()
        self.cid = new_conversation()

    def patch(self, **body):
        return self.http.patch(f"/api/conversations/{self.cid}", json=body, headers=ORIGIN)

    def test_modes(self):
        self.patch(screen_control=True, screen_allow=["notepad.exe"])
        self.assertEqual(conversations.get(self.cid)["screen_mode"], "fenetres")
        self.patch(screen_control=True, screen_mode="pc")
        conv = conversations.get(self.cid)
        self.assertEqual(conv["screen_mode"], "pc")
        self.assertNotIn("screen_allow", conv)  # plus de liste : tout le PC
        self.assertIsNone(tools._screen_allow(self.cid))
        self.assertEqual(self.patch(screen_mode="partout").status_code, 400)
        self.patch(screen_control=False)
        self.assertNotIn("screen_mode", conversations.get(self.cid))  # la main rendue : le mode tombe


if __name__ == "__main__":
    unittest.main()
