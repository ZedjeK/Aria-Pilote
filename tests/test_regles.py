"""Règles « toujours autoriser » : correspondance, enchaînements refusés, accords, API."""

import asyncio
import json
import unittest

from base import ORIGIN, client, new_conversation, workspace

from aria_pilote import permissions, toolrun

ATTEMPTS = [
    "python -m unittest; Remove-Item -Recurse C:\\x",
    "python -m unittest && del x",
    "python -m unittest || del x",
    "python -m unittest | Out-File x",
    "python -m unittest > C:\\Windows\\x",
    "python -m unittest < entree.txt",
    "python -m unittest $(Remove-Item x)",
    "python -m unittest `\nRemove-Item x",
    "python -m unittest\nRemove-Item x",
    "python -m unittest\r\nRemove-Item x",
    "python -m unittest & calc",
    "python -m unittest @(Remove-Item x)",
    "python -m unittest {Remove-Item x}",
    "python -m unittest $env:X",
]


class Matching(unittest.TestCase):
    def test_exacte_casse_et_espaces_ignores(self):
        self.assertTrue(permissions.matches("git status", "  Git   STATUS "))
        self.assertFalse(permissions.matches("git status", "git status --short"))

    def test_exacte_retour_a_la_ligne_refuse(self):
        self.assertFalse(permissions.matches("echo hello", "echo\nhello"))

    def test_prefixe(self):
        self.assertTrue(permissions.matches("python -m unittest*", "python -m unittest test_stats -v"))
        self.assertTrue(permissions.matches("python -m unittest*", "python -m unittest"))
        self.assertFalse(permissions.matches("python -m unittest*", "python -m unittest_maison"))

    def test_enchainements_tous_refuses(self):
        for attempt in ATTEMPTS:
            with self.subTest(commande=attempt):
                self.assertFalse(permissions.matches("python -m unittest*", attempt))

    def test_suggestions_de_prefixe(self):
        self.assertEqual(permissions.suggest_prefix("python -m unittest test_stats"), "python -m unittest*")
        self.assertEqual(permissions.suggest_prefix("python -m unittest"), "python -m unittest*")
        self.assertEqual(permissions.suggest_prefix("git status"), "git status*")
        self.assertEqual(permissions.suggest_prefix("git log -5 | Select-String fix"), "git log*")
        self.assertEqual(permissions.suggest_prefix("Get-ChildItem -Recurse -Filter *.log"), "Get-ChildItem*")


class Rules(unittest.TestCase):
    def setUp(self):
        self.root = workspace()

    def test_regles_invalides_refusees(self):
        for rule, command in [("git *", "python -m unittest"), ("python -m unittest; rm*", "python -m unittest; rm x"),
                              ("python * unittest", "python -m unittest"), ("*", "git status")]:
            with self.subTest(regle=rule), self.assertRaises(permissions.RuleError):
                permissions.add(self.root, rule, command)

    def test_ajout_cloisonnement_et_persistance(self):
        from aria_pilote import config
        permissions.add(self.root, "python -m unittest*", "python -m unittest test_stats")
        permissions.add(self.root, "git log -5 | Select-String fix", "git log -5 | Select-String fix")
        self.assertIsNotNone(permissions.find(self.root, "git log -5 | select-string fix"))
        self.assertIsNone(permissions.find(workspace("autre"), "python -m unittest"))
        config._data = None  # relit config.json, comme au redémarrage
        self.assertIn("python -m unittest*", permissions.rules(self.root))


class Approvals(unittest.TestCase):
    def setUp(self):
        self.root = workspace()
        self.cid = new_conversation(self.root)

    def run_command(self, command):
        async def go():
            out, evs = {}, []
            async for ev in toolrun.run_tool(self.cid, str(self.root), "run_command", {"command": command}, "c1", out):
                evs.append(json.loads(ev[6:]))
                if evs[-1]["type"] == "approval":
                    toolrun.resolve_approval(evs[-1]["approval_id"], "deny")
            return out["result"], evs
        return asyncio.run(go())

    def test_commande_couverte_sans_accord_et_enchainement_redemande(self):
        _, evs = self.run_command('python -c "print(1)"')
        self.assertTrue(any(e["type"] == "approval" for e in evs))
        permissions.add(self.root, "python -m unittest*", "python -m unittest")
        result, evs = self.run_command("python -m unittest --help")
        self.assertEqual(evs, [])
        self.assertEqual((result["status"], result.get("rule")), ("ok", "python -m unittest*"))
        _, evs = self.run_command("python -m unittest --help; echo piège")
        self.assertTrue(any(e["type"] == "approval" for e in evs))

    def test_liste_noire_prioritaire(self):
        permissions.add(self.root, "format-volume*", "format-volume -DriveLetter Z")
        result, evs = self.run_command("Format-Volume -DriveLetter Z")
        self.assertEqual(result["status"], "error")
        self.assertIn("liste noire", result["output"])
        self.assertEqual(evs, [])

    def decide(self, decision, rule):
        async def go():
            out = {}
            gen = toolrun.run_tool(self.cid, str(self.root), "run_command", {"command": "git status --short"}, "c2", out)
            ev = json.loads((await gen.__anext__())[6:])
            error = None
            try:
                toolrun.resolve_approval(ev["approval_id"], decision, rule)
            except toolrun.ApprovalError as exc:
                error = str(exc)
                toolrun.resolve_approval(ev["approval_id"], "deny")
            async for _ in gen:
                pass
            return error, ev
        return asyncio.run(go())

    def test_regle_envoyee_verifiee_par_le_serveur(self):
        error, ev = self.decide("allow_rule", "rm *")
        self.assertIn("ne couvre pas", error)
        self.assertEqual(ev["rule_prefix"], "git status*")
        error, _ = self.decide("allow_rule", "git status*")
        self.assertIsNone(error)
        self.assertIn("git status*", permissions.rules(self.root))

    def test_api_liste_et_retrait(self):
        api = client()
        permissions.add(self.root, "git status*", "git status")
        self.assertIn("git status*", api.get(f"/api/agent/rules?conversation_id={self.cid}").json())
        after = api.request("DELETE", "/api/agent/rules", json={"conversation_id": self.cid, "rule": "git status*"},
                            headers=ORIGIN).json()
        self.assertNotIn("git status*", after)
        self.assertEqual(api.request("DELETE", "/api/agent/rules", json={"conversation_id": self.cid, "rule": "x"},
                                     headers=ORIGIN).status_code, 404)


if __name__ == "__main__":
    unittest.main()
