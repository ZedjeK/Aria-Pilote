"""Contrôle du PC, étape 5A : menus et boîtes de dialogue rattachés à leur fenêtre, fenêtre réduite rouverte, zoom."""
import unittest
from unittest import mock

from PIL import Image

from base import new_conversation  # en premier : Aria tourne dans un dossier temporaire

from aria_pilote import conversations, screen, tools
from aria_pilote.server import ServerManager

WINDOWS = [
    {"id": 101, "titre": "Sans titre - Bloc-notes", "processus": "Notepad.exe", "rect": (100, 100, 900, 700), "reduite": False},
    {"id": 202, "titre": "Calculatrice", "processus": "CalculatorApp.exe", "rect": (0, 0, 400, 600), "reduite": True},
    {"id": 303, "titre": "Paramètres", "processus": "SystemSettings.exe", "rect": (0, 0, 400, 600), "reduite": True},
]
MENU = {"id": 555, "titre": "", "classe": "#32768", "rect": (850, 300, 1050, 500)}
MENU_ITEMS = [{"n": 1, "type": "MenuItemControl", "nom": "Coller", "rect": (860, 310, 1040, 330), "valeur": None,
               "mot_de_passe": False, "actif": True}]
NOTEPAD_ITEMS = [{"n": 2, "type": "DocumentControl", "nom": "Texte", "rect": (110, 150, 890, 690), "valeur": "",
                  "mot_de_passe": False, "actif": True}]


class Fakes(unittest.TestCase):
    def setUp(self):
        self.cid = new_conversation()
        self.calls = []
        self.popups = []
        self.fg = 0
        fakes = {
            "windows": lambda: WINDOWS, "popups": lambda w: self.popups,
            "read_elements": lambda w, extra=(): (MENU_ITEMS + NOTEPAD_ITEMS if extra else NOTEPAD_ITEMS, [], False),
            "capture_window": lambda w: (Image.new("RGB", (800, 600), "white"), (100, 100)),
            "capture_region": lambda r: (self.calls.append(("region", tuple(round(v) for v in r))),
                                         Image.new("RGB", (round(r[2] - r[0]), round(r[3] - r[1])), "white"))[1],
            "foreground": lambda: self.fg, "owner_at": lambda x, y: 555 if x >= 850 else 101,
            "bring_to_front": lambda h: self.calls.append(("front", h)), "restore": lambda h: self.calls.append(("restore", h)),
            "focused_is_password": lambda: False,
            "click": lambda *a, **k: self.calls.append(("click", a)),
        }
        for name, fake in fakes.items():
            patcher = mock.patch.object(screen, name, side_effect=fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        for target, value in ((screen, "SETTLE_S"), ):
            patcher = mock.patch.object(target, value, 0)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(ServerManager, "vision", new_callable=mock.PropertyMock, return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def bind(self, **args):
        return tools.bind_screen_action(tools.prepare_screen_action(None, args), self.cid)


class MenusTest(Fakes):
    def test_menu_ouvert_observe_avec_sa_fenetre(self):
        self.popups = [MENU]
        result = tools._see_screen("Bloc-notes", self.cid)
        self.assertIn("Ouvert par-dessus : menu", result["output"])
        self.assertIn("« Coller »", result["output"])
        state = conversations.get(self.cid)["screen_state"]
        self.assertEqual(state["zone"], [100, 100, 1050, 700])  # la fenêtre et le menu qui déborde
        self.assertIn(("region", (100, 100, 1050, 700)), self.calls)  # capture de l'écran : le menu y figure

    def test_clic_dans_le_menu_accepte_sans_le_refermer(self):
        self.popups = [MENU]
        tools._see_screen("Bloc-notes", self.cid)
        self.fg = 555  # le menu a le focus : remettre la fenêtre devant le refermerait
        p = self.bind(action="cliquer", element=1)
        tools._act(p, self.cid)
        self.assertEqual([c[1][:2] for c in self.calls if c[0] == "click"], [(950.0, 320.0)])  # au centre de « Coller »
        self.assertNotIn(("front", 101), self.calls)

    def test_fenetre_d_une_autre_appli_toujours_refusee(self):
        tools._see_screen("Bloc-notes", self.cid)
        p = self.bind(action="cliquer", element=2)
        with mock.patch.object(screen, "owner_at", side_effect=lambda x, y: 999):
            with self.assertRaises(tools.ToolError):
                tools._act(p, self.cid)

    def test_dialogue_decrit_par_son_titre(self):
        dialog = {"id": 777, "titre": "Enregistrer sous", "classe": "#32770", "rect": (200, 200, 800, 600)}
        text = screen.describe_elements(WINDOWS[0], [], [], False, [dialog])
        self.assertIn("boîte de dialogue « Enregistrer sous »", text)


class RestoreTest(Fakes):
    def test_rouvrir_une_fenetre_reduite(self):
        p = self.bind(action="restaurer", fenetre="Calculatrice")
        self.assertTrue(p["preview"].startswith("Rouvrir la fenêtre « Calculatrice »"))
        with mock.patch.object(screen, "windows", side_effect=lambda: [{**WINDOWS[1], "reduite": False}]):
            result = tools._act(p, self.cid)
        self.assertIn(("restore", 202), self.calls)
        self.assertIn("Fait : Rouvrir", result["output"])
        self.assertEqual(conversations.get(self.cid)["screen_state"]["window"], 202)  # observée dans la foulée

    def test_refus(self):
        with self.assertRaises(tools.ToolError):
            tools.prepare_screen_action(None, {"action": "restaurer"})  # sans fenêtre
        with self.assertRaises(tools.ToolError):
            self.bind(action="restaurer", fenetre="Paramètres")  # appli interdite d'office

    def test_fenetre_reduite_oriente_vers_restaurer(self):
        tools._remember_screen(self.cid, WINDOWS[1], [])
        with self.assertRaises(tools.ToolError) as ctx:
            self.bind(action="touches", touches="entree")
        self.assertIn("restaurer", str(ctx.exception))


class ZoomTest(Fakes):
    def test_zoom_sur_la_derniere_capture(self):
        tools._see_screen("Bloc-notes", self.cid)  # capture 800x600 à l'échelle 1, origine (100, 100)
        prepared = tools.prepare_screen(None, {"zone": [10, 50, 210, 150]})
        result = tools._see_screen(prepared["fenetre"], self.cid, prepared["zone"])
        self.assertIn("agrandie ×3.0", result["output"])
        self.assertIn(("region", (110, 150, 310, 250)), self.calls)
        capture = conversations.get(self.cid)["screen_state"]["capture"]
        self.assertEqual((capture["origin"], capture["scale"]), ([110, 150], 3))
        # Un point du zoom tombe au bon endroit de l'écran
        tools._act(self.bind(action="cliquer", x=30, y=30), self.cid)
        self.assertEqual([c[1][:2] for c in self.calls if c[0] == "click"][-1], (120.0, 160.0))

    def test_zones_refusees(self):
        for zone in ([0, 0, 3, 3], [1, 2, 3], "tout"):
            with self.subTest(zone=zone), self.assertRaises(tools.ToolError):
                tools.prepare_screen(None, {"zone": zone})
        with self.assertRaises(tools.ToolError):
            tools._see_screen("", self.cid, [0, 0, 100, 100])  # rien de capturé avant


if __name__ == "__main__":
    unittest.main()
