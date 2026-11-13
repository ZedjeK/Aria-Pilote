"""Navigateur piloté, actions : cliquer, taper, choisir ; accords, boutons sensibles, mots de passe masqués et jamais
remplis, alertes et choix de fichier interceptés.

Les vrais lancements demandent Chromium (tools/install_navigateur.py) : sans lui, ils sont sautés, comme en CI.
"""

import asyncio
import json
import sys
import unittest

from base import SRC, new_conversation

from aria_pilote import agent, config, conversations, navigateur, page_texte, toolrun, tools

DEV_CHROMIUM = SRC / "chromium"


def fake_view(cid, **changes):
    """Une lecture de page comme celle que laisse _snapshot, sans navigateur."""
    vue = {"url": "https://exemple.fr/compte", "refs": {4: "e4", 6: "e6", 7: "e7", 8: "e8", 9: "e9", 12: "e12"},
           "roles": {4: "textbox", 6: "textbox", 7: "combobox", 8: "button", 9: "button", 12: "link"},
           "labels": {4: "[4] champ « Pseudo »", 6: tools.PASSWORD_LINE.format(n=6), 7: "[7] liste déroulante « Pays »",
                      8: "[8] bouton « Se connecter »", 9: "[9] bouton « Payer 49 € »", 12: "[12] lien « Aide »"},
           "passwords": {6}}
    vue.update(changes)
    navigateur.manager.vues[cid] = vue
    return vue


class Preparation(unittest.TestCase):
    def setUp(self):
        self.cid = new_conversation()
        self.addCleanup(navigateur.manager.vues.pop, self.cid, None)

    def bind(self, name, **args):
        tool = tools.TOOLS[name]
        return tool.bind(tool.prepare(None, args), self.cid)

    def test_rien_sans_lecture(self):
        with self.assertRaises(tools.ToolError) as ctx:
            self.bind("cliquer", element=12)
        self.assertIn("Lis d'abord la page", str(ctx.exception))

    def test_element_inconnu_ou_mal_forme(self):
        fake_view(self.cid)
        with self.assertRaises(tools.ToolError):
            self.bind("cliquer", element=999)
        for bad in ("abc", 0, -3, None):
            with self.assertRaises(tools.ToolError, msg=bad):
                tools.prepare_cliquer(None, {"element": bad})
        self.assertEqual(tools.prepare_cliquer(None, {"element": "[e12]"})["element"], 12)

    def test_jamais_de_mot_de_passe(self):
        fake_view(self.cid)
        with self.assertRaises(tools.ToolError) as ctx:
            self.bind("taper", element=6, texte="secret")
        self.assertIn("mot de passe", str(ctx.exception))

    def test_taper_et_choisir_seulement_sur_le_bon_element(self):
        fake_view(self.cid)
        with self.assertRaises(tools.ToolError):
            self.bind("taper", element=12, texte="x")  # un lien
        with self.assertRaises(tools.ToolError):
            self.bind("choisir", element=4, option="France")  # un champ de texte
        self.assertEqual(self.bind("choisir", element=7, option="Japon")["option"], "Japon")

    def test_action_ordinaire_et_apercu(self):
        fake_view(self.cid)
        p = self.bind("taper", element=4, texte="jeremy")
        self.assertTrue(p["ordinary"])
        self.assertIn("Taper dans [4] champ « Pseudo »", p["preview"])
        self.assertIn("Texte : jeremy", p["preview"])
        self.assertIn("https://exemple.fr/compte", p["preview"])
        self.assertTrue(self.bind("cliquer", element=12)["ordinary"])

    def test_boutons_sensibles_toujours_demandes(self):
        fake_view(self.cid)
        pay = self.bind("cliquer", element=9)
        self.assertFalse(pay["ordinary"])
        self.assertIn("sensible", pay["preview"])
        # Page avec un mot de passe : son bouton est toujours demandé, même s'il ne dit pas « envoyer »
        self.assertFalse(self.bind("cliquer", element=8)["ordinary"])
        # Valider par Entrée un champ d'une page à mot de passe : toujours demandé
        self.assertFalse(self.bind("taper", element=4, texte="jeremy", valider=True)["ordinary"])
        # Sans mot de passe dans la page, un bouton banal est ordinaire
        fake_view(self.cid, passwords=set())
        self.assertTrue(self.bind("cliquer", element=8)["ordinary"])


class Accords(unittest.TestCase):
    def setUp(self):
        self.cid = new_conversation()
        conversations.update(self.cid, lambda c: c.__setitem__("task_id", "tache-1"))
        self.addCleanup(navigateur.manager.vues.pop, self.cid, None)
        fake_view(self.cid)

    def run_tool(self, name, args, decision=None):
        """Lance l'outil comme l'agent ; si un accord est demandé, répond `decision`. Renvoie (accord demandé, résultat)."""
        out, asked = {}, []

        async def scenario():
            async for ev in toolrun.run_tool(self.cid, None, name, args, "appel", out):
                payload = json.loads(ev.removeprefix("data: "))  # événement SSE : « data: {…} »
                if payload["type"] == "approval":
                    asked.append(payload)
                    toolrun.resolve_approval(payload["approval_id"], decision or "deny")

        asyncio.run(scenario())
        return asked, out["result"]

    def test_chaque_action_est_demandee(self):
        asked, result = self.run_tool("cliquer", {"element": 12})
        self.assertEqual(len(asked), 1)
        self.assertEqual(asked[0]["kind"], "page_action")
        self.assertTrue(asked[0]["ordinary"])
        self.assertEqual(result["status"], "denied")

    def test_actions_ordinaires_permises_pour_la_tache(self):
        asked, _ = self.run_tool("cliquer", {"element": 12}, decision="allow_task")
        self.assertEqual(len(asked), 1)
        self.assertEqual(conversations.get(self.cid)["page_auto_task"], "tache-1")
        self.assertNotIn("screen_auto_task", conversations.get(self.cid))  # l'écran garde sa propre clé
        # L'action ordinaire suivante passe sans accord (elle échoue ensuite : aucun onglet n'est ouvert ici)
        asked, result = self.run_tool("cliquer", {"element": 12})
        self.assertEqual(asked, [])
        self.assertIn("Aucune page ouverte", result["output"])
        # Un bouton sensible reste demandé
        asked, result = self.run_tool("cliquer", {"element": 9})
        self.assertEqual(len(asked), 1)
        self.assertEqual(result["status"], "denied")
        # La tâche suivante redemande
        conversations.update(self.cid, lambda c: c.__setitem__("task_id", "tache-2"))
        asked, _ = self.run_tool("cliquer", {"element": 12})
        self.assertEqual(len(asked), 1)

    def test_verrou_du_plan_et_disponibilite(self):
        for name in ("cliquer", "taper", "choisir"):
            self.assertIn(agent.tool_kind(name), agent.LOCKED_KINDS)
            self.assertIn(name, agent.allowed_tools(folder=False))
        web = config.get().get("web")
        self.addCleanup(config.update, {"web": web})
        config.update({"web": {**web, "enabled": False}})
        for name in ("cliquer", "taper", "choisir"):
            self.assertNotIn(name, agent.allowed_tools(folder=False))
            self.assertNotIn(name, agent.allowed_tools(folder=True))

    def test_roles(self):
        snap = '- textbox "Pseudo" [ref=e4]: jeremy\n- button "OK" [ref=e8]\n- generic [ref=e9]'
        self.assertEqual(page_texte.roles(snap), {4: "textbox", 8: "button"})


FORM = """<title>Compte</title>
<form onsubmit="event.preventDefault(); document.getElementById('etat').textContent = 'envoyé : ' + this.u.value">
<label>Pseudo <input name="u"></label>
<label>Mot de passe <input type="password" name="p" value="secret123"></label>
<select aria-label="Pays"><option>France</option><option>Japon</option></select>
<button type="submit">Se connecter</button>
</form>
<p id="etat">rien</p>
<button onclick="alert('Attention, ceci est une alerte')">Alerte</button>
<input type="file" aria-label="Fichier">
<a href="#ailleurs" onclick="document.body.innerHTML = '<p>autre page</p>'">Ailleurs</a>"""


@unittest.skipUnless(sys.platform == "win32" and any(DEV_CHROMIUM.glob("chromium-*/chrome-win64/chrome.exe")),
                     "Chromium non installé (tools/install_navigateur.py)")
class VraiesActions(unittest.TestCase):
    def setUp(self):
        self.original = navigateur.BROWSERS_DIR, navigateur.manager
        navigateur.BROWSERS_DIR = DEV_CHROMIUM
        navigateur.manager = navigateur.Navigateur()  # chaque test a sa propre boucle asyncio
        self.cid = new_conversation()

    def tearDown(self):
        navigateur.BROWSERS_DIR, navigateur.manager = self.original

    def run_scenario(self, scenario):
        async def wrapper():
            try:
                await scenario()
            finally:
                await navigateur.manager.fermer()

        asyncio.run(wrapper())

    async def form(self):
        page = await navigateur.manager.page(self.cid)
        await page.set_content(FORM)
        return page

    async def act(self, name, **args):
        tool = tools.TOOLS[name]
        prepared = await asyncio.to_thread(tool.bind, tool.prepare(None, args), self.cid)
        return await tool.run(prepared, self.cid)

    def number(self, lines, needle):
        line = next(line for line in lines if needle in line)
        return int(line[1:].split("]")[0])

    def test_mot_de_passe_jamais_montre_au_modele(self):
        async def scenario():
            await self.form()
            text = (await tools.run_lire_page({"partie": 1}, self.cid))["output"]
            self.assertNotIn("secret123", text)
            self.assertIn("champ mot de passe (contenu masqué", text)
            found = await tools.run_chercher_page({"texte": "secret"}, self.cid)
            self.assertNotIn("secret123", found["output"])
            self.assertEqual(len(navigateur.manager.vues[self.cid]["passwords"]), 1)

        self.run_scenario(scenario)

    def test_taper_choisir_puis_envoyer(self):
        async def scenario():
            page = await self.form()
            lines, _ = await tools._snapshot(self.cid, page)
            pseudo, pays = self.number(lines, "« Pseudo »"), self.number(lines, "« Pays »")
            done = await self.act("taper", element=pseudo, texte="jeremy")
            self.assertIn("Fait : taper dans", done["output"])
            self.assertIn("Même adresse", done["output"])
            await self.act("choisir", element=pays, option="Japon")
            self.assertEqual(await page.locator("select").input_value(), "Japon")
            lines, _ = await tools._snapshot(self.cid, page)
            await self.act("cliquer", element=self.number(lines, "« Se connecter »"))
            self.assertEqual(await page.locator("#etat").text_content(), "envoyé : jeremy")

        self.run_scenario(scenario)

    def test_alerte_refermee_et_fichier_refuse(self):
        async def scenario():
            page = await self.form()
            lines, _ = await tools._snapshot(self.cid, page)
            alert = await self.act("cliquer", element=self.number(lines, "« Alerte »"))
            self.assertIn("refermée sans l'accepter", alert["output"])
            self.assertIn("Attention, ceci est une alerte", alert["output"])
            lines, _ = await tools._snapshot(self.cid, page)
            chooser = await self.act("cliquer", element=self.number(lines, "« Fichier »"))
            self.assertIn("aucun fichier", chooser["output"])

        self.run_scenario(scenario)

    def test_nouvelle_page_et_lecture_perimee(self):
        async def scenario():
            page = await self.form()
            lines, _ = await tools._snapshot(self.cid, page)
            moved = await self.act("cliquer", element=self.number(lines, "« Ailleurs »"))
            self.assertIn("Nouvelle page", moved["output"])
            self.assertIn("autre page", moved["output"])
            # La page change sans que l'agent l'ait relue : il doit relire avant d'agir
            stale = dict(navigateur.manager.vues[self.cid], url="about:blank#ancienne")
            navigateur.manager.vues[self.cid] = stale
            with self.assertRaises(tools.ToolError) as ctx:
                await self.act("cliquer", element=next(iter(stale["refs"]), 1))
            self.assertIn("relis-la", str(ctx.exception))

        self.run_scenario(scenario)

    def test_voir_page_jusqu_a_un_element(self):
        async def scenario():
            page = await navigateur.manager.page(self.cid)
            await page.set_content("<div style='height:3000px'>haut</div><button>Tout en bas</button>")
            lines, _ = await tools._snapshot(self.cid, page)
            bottom = self.number(lines, "« Tout en bas »")
            seen = await tools.run_voir_page({"element": bottom}, self.cid)
            self.assertIn(f"[{bottom}] bouton « Tout en bas »", seen["output"])  # visible après défilement

        self.run_scenario(scenario)


if __name__ == "__main__":
    unittest.main()
