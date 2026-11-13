"""Navigateur piloté : le Chromium embarqué, son profil à part, sa fermeture et son nettoyage.

Les vrais lancements demandent Chromium (tools/install_navigateur.py) : sans lui, ils sont sautés, comme en CI qui
ne télécharge aucun binaire.
"""

import asyncio
import shutil
import sys
import unittest
from pathlib import Path

from base import BIN, SRC, sent_to_bin

from aria_pilote import config, jobs, navigateur

# Le Chromium du code source, s'il est installé : les tests le lisent sans jamais y écrire
DEV_CHROMIUM = SRC / "chromium"


def fake_exe(base, revision):
    exe = base / f"chromium-{revision}" / "chrome-win64" / "chrome.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("faux", encoding="utf-8")
    return exe


class Emplacements(unittest.TestCase):
    def test_tout_reste_dans_le_dossier_de_l_appli(self):
        self.assertEqual(navigateur.BROWSERS_DIR, config.ROOT / "chromium")
        self.assertEqual(navigateur.PROFILE_DIR, config.ROOT / "data" / "navigateur" / "profil")

    def test_executable_le_plus_recent(self):
        base = config.ROOT / "faux-chromium"
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        original = navigateur.BROWSERS_DIR
        navigateur.BROWSERS_DIR = base
        self.addCleanup(setattr, navigateur, "BROWSERS_DIR", original)
        self.assertIsNone(navigateur.executable())  # dossier absent
        fake_exe(base, 999)
        newest = fake_exe(base, 1254)
        fake_exe(base, 1100)
        (base / "chromium-sans-numero" / "chrome-win64").mkdir(parents=True)
        self.assertEqual(navigateur.executable(), newest)  # 1254 > 1100 > 999, comparés comme des nombres


class SansChromium(unittest.TestCase):
    def setUp(self):
        self.manager = navigateur.Navigateur()

    def test_ouvrir_sans_chromium_explique_quoi_faire(self):
        original = navigateur.BROWSERS_DIR
        navigateur.BROWSERS_DIR = config.ROOT / "absent"
        self.addCleanup(setattr, navigateur, "BROWSERS_DIR", original)
        with self.assertRaises(navigateur.NavigateurError) as ctx:
            asyncio.run(self.manager.ouvrir())
        self.assertIn("install_navigateur.py", str(ctx.exception))
        self.assertFalse(self.manager.ouvert)
        self.assertFalse(self.manager.status()["installe"])

    def test_fermer_sans_navigateur_ouvert(self):
        self.assertFalse(asyncio.run(self.manager.fermer()))

    def test_vider_le_profil_l_envoie_a_la_corbeille(self):
        (navigateur.PROFILE_DIR / "Default").mkdir(parents=True, exist_ok=True)
        (navigateur.PROFILE_DIR / "Default" / "Cookies").write_text("cookie", encoding="utf-8")
        before = len(sent_to_bin)
        self.assertTrue(asyncio.run(self.manager.vider_profil()))
        self.assertFalse(navigateur.PROFILE_DIR.exists())
        self.assertEqual(sent_to_bin[before:], ["profil"])
        self.assertTrue(any(p.name.endswith("-profil") for p in BIN.iterdir()))  # récupérable
        self.assertFalse(asyncio.run(self.manager.vider_profil()))  # plus rien à vider


@unittest.skipUnless(sys.platform == "win32" and any(DEV_CHROMIUM.glob("chromium-*/chrome-win64/chrome.exe")),
                     "Chromium non installé (tools/install_navigateur.py)")
class VraiChromium(unittest.TestCase):
    """Un vrai Chromium, visible, sur le profil du dossier de test."""

    def setUp(self):
        self.original = navigateur.BROWSERS_DIR
        navigateur.BROWSERS_DIR = DEV_CHROMIUM
        self.manager = navigateur.Navigateur()

    def tearDown(self):
        navigateur.BROWSERS_DIR = self.original

    def run_scenario(self, scenario):
        """Le scénario, puis la fermeture, dans la même boucle asyncio que l'ouverture (comme dans l'appli)."""
        async def wrapper():
            try:
                await scenario()
            finally:
                await self.manager.fermer()

        asyncio.run(wrapper())

    def test_ouvrir_lire_une_page_puis_fermer(self):
        async def scenario():
            context = await self.manager.ouvrir()
            self.assertIs(await self.manager.ouvrir(), context)  # un seul navigateur, pas un second
            self.assertTrue(self.manager.status()["ouvert"])
            page = context.pages[0] if context.pages else await context.new_page()
            await page.set_content("<h1>Essai Aria</h1><button>OK</button>")
            snapshot = await page.locator("body").aria_snapshot()
            self.assertIn('button "OK"', snapshot)
            # Le pilote Node est rattaché au job d'Aria : il mourrait avec elle (attribut interne de Playwright)
            self.assertTrue(self.manager._attach_driver())
            self.assertTrue(await self.manager.fermer())
            self.assertFalse(self.manager.ouvert)

        self.run_scenario(scenario)
        self.assertTrue((navigateur.PROFILE_DIR / "Default").is_dir())  # le profil est celui d'Aria

    def test_fenetre_fermee_par_l_utilisateur(self):
        async def scenario():
            context = await self.manager.ouvrir()
            # Comme la croix de la fenêtre : le contexte se ferme, et le gestionnaire arrête Playwright en même temps
            await asyncio.gather(context.close(), return_exceptions=True)
            for _ in range(50):
                if not self.manager.ouvert:
                    break
                await asyncio.sleep(0.1)
            self.assertFalse(self.manager.ouvert)
            reopened = await self.manager.ouvrir()  # et il se rouvre normalement
            self.assertIsNot(reopened, context)

        self.run_scenario(scenario)

    def test_vider_le_profil_refuse_quand_il_est_ouvert(self):
        async def scenario():
            await self.manager.ouvrir()
            with self.assertRaises(navigateur.NavigateurError):
                await self.manager.vider_profil()

        self.run_scenario(scenario)


if __name__ == "__main__":
    unittest.main()
