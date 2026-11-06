"""Vérification de fin de tâche : quand l'agent a modifié des fichiers et annonce avoir fini, les hooks « verifier »
tournent ; un échec lui renvoie leur sortie pour qu'il corrige, 2 fois au plus."""

import asyncio
import json
import shutil
import time
import unittest
from unittest import mock

from base import events, new_conversation, workspace

from aria_pilote import agent, chat, conversations, hooks, toolrun

PRESET = {"id": "test", "max_tokens": 1024}
WRITE = ("write_file", {"path": "a.py", "content": "x = 1\n"})
READ = ("read_file", {"path": "a.py"})


def outcome(code, sortie="", commande="python -m unittest"):
    return {"source": "projet", "commande": commande, "code": code, "sortie": sortie}


class AgentVerification(unittest.TestCase):
    def setUp(self):
        self.root = workspace()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.cid = new_conversation(self.root)
        conversations.append_message(self.cid, {"role": "user", "content": "Corrige le projet."})
        self.payloads, self.checks = [], []

    def run_agent(self, steps, verdicts):
        """Joue l'agent : `steps` sont les réponses du modèle (texte final, ou appel d'outil), `verdicts` les
        résultats successifs des hooks « verifier »."""
        steps, verdicts = list(steps), list(verdicts)

        async def fake_completion(payload, out):
            self.payloads.append(payload)
            out.update(content=[], reasoning=[], tool_calls={}, stats={"finish_reason": "stop"},
                       started=time.time(), first_token_at=None, finished=True)
            step = steps.pop(0)
            if isinstance(step, str):
                out["content"].append(step)
            else:
                out["tool_calls"][0] = {"id": f"c{len(self.payloads)}", "type": "function",
                                        "function": {"name": step[0], "arguments": json.dumps(step[1])}}
            return
            yield

        async def fake_tool(cid, root, name, args, call_id, holder, allowed=None):
            holder["result"] = {"status": "ok", "output": "fait"}
            return
            yield

        async def fake_hooks(event, root, *args, **kwargs):
            if event != "verifier":
                return []
            self.checks.append(root)
            return verdicts.pop(0) if verdicts else []

        async def no_room(*args, **kwargs):
            return None

        async def collect():
            return [ev async for ev in agent.run(conversations.get(self.cid), PRESET, "Simu")]

        with mock.patch.object(chat, "completion", fake_completion), mock.patch.object(chat, "make_room", no_room), \
                mock.patch.object(toolrun, "run_tool", fake_tool), mock.patch.object(hooks, "run", fake_hooks):
            raw = asyncio.run(collect())
        return events(raw)

    def feedback(self):
        return [m for m in conversations.get(self.cid)["messages"] if m["role"] == "verification"]

    def test_pas_de_verification_sans_ecriture(self):
        evs = self.run_agent([READ, "Voilà le contenu."], [[outcome(1, "FAILED")]])
        self.assertEqual(self.checks, [])
        self.assertEqual(evs[-1]["type"], "done")

    def test_reussite_du_premier_coup(self):
        evs = self.run_agent([WRITE, "C'est corrigé."], [[outcome(0, "OK")]])
        self.assertEqual(len(self.checks), 1)
        self.assertEqual(self.feedback(), [])
        self.assertIn("verifier", [e.get("evenement") for e in evs if e["type"] == "hook"])
        self.assertEqual(evs[-1]["type"], "done")
        self.assertFalse([e for e in evs if e["type"] == "error"])

    def test_echec_renvoye_au_modele_puis_corrige(self):
        evs = self.run_agent([WRITE, "Fini.", WRITE, "Fini, pour de bon."],
                             [[outcome(1, "FAILED (failures=1)")], [outcome(0, "OK")]])
        self.assertEqual(len(self.checks), 2)
        [note] = self.feedback()
        self.assertIn("(1/2)", note["content"])
        self.assertIn("FAILED (failures=1)", note["content"])
        # Le modèle le reçoit comme un message, juste après sa réponse « Fini. »
        sent = self.payloads[2]["messages"]
        self.assertEqual(sent[-1], {"role": "user", "content": note["content"]})
        self.assertEqual(sent[-2]["content"], "Fini.")
        self.assertEqual(evs[-1]["type"], "done")
        self.assertFalse([e for e in evs if e["type"] == "error"])

    def test_deux_relances_au_plus(self):
        failed = [outcome(1, "FAILED")]
        writes = [("write_file", {"path": "a.py", "content": f"x = {n}\n"}) for n in range(3)]  # 3 essais différents
        evs = self.run_agent([writes[0], "Fini.", writes[1], "Fini.", writes[2], "Fini."], [failed, failed, failed])
        self.assertEqual(len(self.checks), 3)
        self.assertEqual(len(self.feedback()), agent.VERIFY_RETRIES)
        errors = [e["message"] for e in evs if e["type"] == "error"]
        self.assertEqual(len(errors), 1)
        self.assertIn("python -m unittest", errors[0])
        self.assertEqual(evs[-1]["type"], "done")

    def test_conclure_sans_corriger_ne_relance_pas(self):
        evs = self.run_agent([WRITE, "Fini.", "Je ne vois pas d'autre correction."], [[outcome(1, "FAILED")]])
        self.assertEqual(len(self.checks), 1)  # rien de modifié depuis : même vérification, même verdict
        self.assertEqual(len([e for e in evs if e["type"] == "error"]), 1)

    def test_sans_hook_verifier_rien_ne_change(self):
        evs = self.run_agent([WRITE, "C'est corrigé."], [])
        self.assertEqual(len(self.checks), 1)
        self.assertFalse([e for e in evs if e["type"] in ("hook", "error")])
        self.assertEqual(evs[-1]["type"], "done")

    def test_ecriture_par_un_sous_agent(self):
        self.assertTrue(agent._wrote("delegate", {"status": "ok", "subagent": {"steps": [
            {"name": "read_file", "status": "ok"}, {"name": "edit_file", "status": "ok"}]}}))
        self.assertFalse(agent._wrote("delegate", {"status": "ok", "subagent": {"steps": [
            {"name": "read_file", "status": "ok"}, {"name": "edit_file", "status": "denied"}]}}))
        self.assertFalse(agent._wrote("write_file", {"status": "denied"}))

    def test_la_demande_reste_la_derniere_du_user(self):
        self.run_agent([WRITE, "Fini.", WRITE, "Fini."], [[outcome(1, "FAILED")], [outcome(0)]])
        conv = conversations.get(self.cid)
        last_user = max(i for i, m in enumerate(conv["messages"]) if m["role"] == "user")
        self.assertEqual(conv["messages"][last_user]["content"], "Corrige le projet.")
        conversations.drop_trailing_assistant(conv)  # Régénérer retire aussi la vérification
        self.assertEqual(conv["messages"][-1]["role"], "user")


class VerifierHook(unittest.TestCase):
    def test_evenement_accepte(self):
        hooks.validate({"verifier": [{"commande": "python -m unittest"}]})

    def test_code_de_la_commande_native_garde(self):
        # PowerShell sort avec 0 après une commande native en échec : un « python -m unittest » rouge passerait au vert
        failing = 'python -c "import sys; print(\'FAILED\'); sys.exit(3)"'
        self.assertEqual(hooks._run_one(failing, ".", {"evenement": "verifier"}), (3, "FAILED"))
        self.assertEqual(hooks._run_one('python -c "print(1)"', ".", {"evenement": "verifier"}), (0, "1"))
        self.assertEqual(hooks._run_one("Write-Output non; exit 2", ".", {"evenement": "avant_outil"}), (2, "non"))

    def test_delai_long_et_fin_de_sortie_gardee(self):
        seen = {}

        def fake_run_one(command, cwd, payload, timeout=hooks.TIMEOUT_S):
            seen[payload["evenement"]] = timeout
            return 1, "début " + "x" * hooks.MAX_OUTPUT_CHARS + " FAILED (failures=2)"

        rules = [("projet", {"commande": "python -m unittest"})]
        with mock.patch.object(hooks, "_rules", lambda event, root: rules), mock.patch.object(hooks, "_run_one", fake_run_one):
            [check] = asyncio.run(hooks.run("verifier", "C:/projet"))
            [end] = asyncio.run(hooks.run("fin_tache", "C:/projet"))
        self.assertEqual(seen, {"verifier": hooks.VERIFY_TIMEOUT_S, "fin_tache": hooks.TIMEOUT_S})
        self.assertTrue(check["sortie"].endswith("FAILED (failures=2)"))  # le résumé des tests, à la fin
        self.assertTrue(end["sortie"].startswith("début"))
        self.assertEqual(len(check["sortie"]), hooks.MAX_OUTPUT_CHARS)


if __name__ == "__main__":
    unittest.main()
