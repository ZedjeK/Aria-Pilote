"""Chantier 4, étape 4B : observer l'écran (fenêtres, éléments numérotés, captures), en lecture seule."""
import unittest
from unittest import mock

from PIL import Image

from base import ORIGIN, client, new_conversation  # en premier : Aria tourne dans un dossier temporaire

from aria_pilote import agent, chat, config, conversations, screen, tools
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


if __name__ == "__main__":
    unittest.main()
