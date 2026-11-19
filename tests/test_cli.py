"""Ligne de commande : sortie, codes de sortie, accord toujours refusé, question, plan, délai. Un vrai serveur HTTP local
(l'appli, sans son démarrage) avec un faux agent : la commande lit un vrai flux SSE et appelle un vrai /api/agent/approve."""
import asyncio
import contextlib
import io
import json
import threading
import time
import unittest
import uuid
from unittest import mock

from base import APP_PORT, workspace  # en premier : Aria tourne dans un dossier temporaire

import httpx
import uvicorn

from aria_pilote import agent, cli, conversations, main, toolrun
from aria_pilote.chat import event
from aria_pilote.server import manager

GOT = {}  # ce que le faux agent a vu : {"decision": …, "plan": …}


def fake_agent(script):
    """Un faux agent.run : `script(conv)` est un générateur asynchrone d'événements."""
    async def run(conv, preset, model_name):
        async for item in script(conv):
            yield item
    return run


async def hello(conv):
    GOT["plan"] = (conv.get("plan") or {}).get("status")
    yield event({"type": "delta", "content": "Je regarde", "reasoning": ""})
    yield event({"type": "tool_call", "call_id": "c1", "name": "read_file", "args": {"path": "README.md"}})
    yield event({"type": "tool_result", "call_id": "c1", "status": "ok", "output": "# Projet"})
    yield event({"type": "step_end"})
    yield event({"type": "delta", "content": "Voilà ", "reasoning": ""})
    yield event({"type": "delta", "content": "la réponse.", "reasoning": ""})
    yield event({"type": "done", "message": {"role": "assistant", "content": "Voilà la réponse."}})


async def needs_approval(conv):
    approval_id = uuid.uuid4().hex[:12]
    future = asyncio.get_running_loop().create_future()
    toolrun._approvals[approval_id] = (future, conv["id"], "command", None, "pip install requests")
    yield event({"type": "tool_call", "call_id": "c1", "name": "run_command", "args": {"command": "pip install requests"}})
    yield event({"type": "approval", "approval_id": approval_id, "call_id": "c1", "kind": "command", "path": None,
                 "preview": "pip install requests", "command": "pip install requests"})
    try:
        GOT["decision"] = await asyncio.wait_for(future, 10)
    finally:
        toolrun._approvals.pop(approval_id, None)
    yield event({"type": "tool_result", "call_id": "c1", "status": "denied", "output": "refusé"})
    yield event({"type": "done", "message": {"role": "assistant", "content": "Je n'ai pas pu installer."}})


async def asks(conv):
    yield event({"type": "tool_call", "call_id": "c1", "name": "ask_user",
                 "args": {"question": "Quelle base ?", "options": ["SQLite", "Postgres"]}})
    yield event({"type": "tool_result", "call_id": "c1", "status": "pending", "output": "question posée"})
    yield event({"type": "done", "message": {"role": "assistant", "content": ""}})


async def plans(conv):
    yield event({"type": "plan", "call_id": "c1", "plan": "1. lire\n2. modifier"})
    yield event({"type": "done", "message": {"role": "assistant", "content": "Voici le plan."}})


async def fails(conv):
    yield event({"type": "error", "message": "Le moteur a planté."})


async def slow(conv):
    yield event({"type": "delta", "content": "début", "reasoning": ""})
    await asyncio.sleep(30)
    yield event({"type": "done", "message": {"role": "assistant", "content": "trop tard"}})


class Server:
    """L'appli sur son vrai port de test, sans les tâches de fond du démarrage (mises à jour, SearXNG, MCP…)."""

    def __enter__(self):
        self.server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=APP_PORT, lifespan="off",
                                                    log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError("serveur d'essai non démarré")

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(10)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = Server().__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.server.__exit__()

    def setUp(self):
        for name, value in [("state", "ready"), ("model", mock.Mock(name="modele"))]:
            patcher = mock.patch.object(manager, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        # /api/status décrit le vrai modèle chargé : ici, seulement son état
        patcher = mock.patch.object(manager, "status", lambda: {"state": manager.state})
        patcher.start()
        self.addCleanup(patcher.stop)

    def use(self, script):
        patcher = mock.patch.object(agent, "run", fake_agent(script))
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, *argv, stdin=None):
        """(code, sortie standard, sortie d'erreur)."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.object(cli.sys, "stdin", io.StringIO(stdin or "")):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()


class Sortie(Base):
    def test_texte_sur_la_sortie_standard_et_suivi_sur_l_erreur(self):
        self.use(hello)
        code, out, err = self.call("-p", "Lis le README")
        self.assertEqual(code, cli.OK)
        self.assertEqual(out, "Voilà la réponse.\n")  # que la réponse : un script peut la reprendre telle quelle
        self.assertIn("▸ read_file README.md", err)
        self.assertRegex(err, r"conversation : [0-9a-f]{12}")

    def test_json(self):
        self.use(hello)
        code, out, err = self.call("--json", "-p", "Lis le README")
        data = json.loads(out)
        self.assertEqual((code, data["code"], data["reponse"]), (0, 0, "Voilà la réponse."))
        self.assertEqual(data["outils"], [{"nom": "read_file", "statut": "ok"}])
        self.assertEqual((data["refus"], data["question"], data["plan"], data["erreur"]), ([], None, None, None))
        self.assertEqual(err, "")  # rien d'autre en mode JSON

    def test_la_conversation_est_dans_l_historique(self):
        self.use(hello)
        _, out, err = self.call("--json", "-p", "Lis le README")
        cid = json.loads(out)["conversation"]
        messages = conversations.get(cid)["messages"]
        self.assertEqual((messages[0]["role"], messages[0]["content"]), ("user", "Lis le README"))

    def test_demande_lue_sur_l_entree_standard(self):
        self.use(hello)
        code, out, err = self.call("--json", "-p", "-", stdin="Lis le README\n")
        cid = json.loads(out)["conversation"]
        self.assertEqual(conversations.get(cid)["messages"][0]["content"].strip(), "Lis le README")

    def test_reprendre_une_conversation(self):
        self.use(hello)
        _, out, _ = self.call("--json", "-p", "Un")
        cid = json.loads(out)["conversation"]
        _, out, _ = self.call("--json", "--conversation", cid, "-p", "Deux")
        self.assertEqual(json.loads(out)["conversation"], cid)
        users = [m["content"] for m in conversations.get(cid)["messages"] if m["role"] == "user"]
        self.assertEqual(users, ["Un", "Deux"])

    def test_plan_d_abord_transmis(self):
        self.use(hello)
        self.call("--plan", "-p", "Corrige le bug")
        self.assertEqual(GOT["plan"], "required")  # le serveur a verrouillé les modifications pour cette demande


class Dossier(Base):
    def test_dossier_attache_a_la_nouvelle_conversation(self):
        self.use(hello)
        root = workspace("cli")
        _, out, _ = self.call("--json", "--dossier", str(root), "-p", "Lis")
        agent_settings = conversations.get(json.loads(out)["conversation"])["agent"]
        self.assertEqual((agent_settings["enabled"], agent_settings["workspace"]), (True, str(root.resolve())))

    def test_dossier_relatif_devient_absolu(self):
        self.use(hello)
        root = workspace("cli")
        with mock.patch.object(cli.os.path, "abspath", return_value=str(root)):
            _, out, _ = self.call("--json", "--dossier", "relatif", "-p", "Lis")
        self.assertTrue(conversations.get(json.loads(out)["conversation"])["agent"]["enabled"])

    def test_dossier_inexistant_refuse_par_le_serveur(self):
        self.use(hello)
        code, out, err = self.call("--dossier", str(workspace("cli") / "absent"), "-p", "Lis")
        self.assertEqual(code, cli.ERROR)
        self.assertIn("Dossier introuvable", err)

    def test_dossier_avec_conversation_refuse(self):
        code, out, err = self.call("--dossier", str(workspace("cli")), "--conversation", "abcdef012345", "-p", "Lis")
        self.assertEqual(code, cli.ERROR)
        self.assertIn("nouvelle conversation", err)


class Codes(Base):
    def test_accord_toujours_refuse(self):
        self.use(needs_approval)
        code, out, err = self.call("--json", "-p", "Installe requests")
        data = json.loads(out)
        self.assertEqual(code, cli.APPROVAL)
        self.assertEqual(GOT["decision"], "deny")  # la commande n'a jamais répondu oui
        self.assertEqual(data["refus"], [{"type": "command", "action": "pip install requests"}])
        self.assertEqual(data["reponse"], "Je n'ai pas pu installer.")  # la tâche a continué sans cette action

    def test_accord_refuse_dit_quoi_faire(self):
        self.use(needs_approval)
        code, out, err = self.call("-p", "Installe requests")
        self.assertEqual(code, cli.APPROVAL)
        self.assertIn("Accord refusé", err)
        self.assertIn("« Toujours autoriser »", err)
        self.assertIn("--conversation", err)

    def test_question_en_attente(self):
        self.use(asks)
        code, out, err = self.call("-p", "Ajoute une base")
        self.assertEqual(code, cli.QUESTION)
        self.assertIn("Quelle base ?", err)
        self.assertIn("SQLite / Postgres", err)
        self.assertRegex(err, r'--conversation [0-9a-f]{12} -p "ta réponse"')

    def test_plan_en_attente(self):
        self.use(plans)
        code, out, err = self.call("--json", "-p", "Refais tout")
        data = json.loads(out)
        self.assertEqual((code, data["plan"]), (cli.PLAN, "1. lire\n2. modifier"))

    def test_erreur_du_moteur(self):
        self.use(fails)
        code, out, err = self.call("-p", "Lis")
        self.assertEqual(code, cli.ERROR)
        self.assertIn("Le moteur a planté.", err)

    def test_delai_depasse(self):
        self.use(slow)
        started = time.monotonic()
        code, out, err = self.call("--delai", "1", "-p", "Lis")
        self.assertEqual(code, cli.TIMEOUT)
        self.assertLess(time.monotonic() - started, 10)
        self.assertIn("Délai dépassé", err)
        self.assertEqual(out.strip(), "début")  # ce qui était déjà arrivé n'est pas perdu


class Preconditions(Base):
    def test_appli_fermee(self):
        closed = httpx.Client(base_url="http://127.0.0.1:1", timeout=2)
        code, out, err = self.call_with(closed, "-p", "Lis")
        self.assertEqual(code, cli.UNREACHABLE)
        self.assertIn("ne répond pas", err)
        self.assertIn("ne la démarre pas", err)

    def call_with(self, client, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv), client=client)
        return code, out.getvalue(), err.getvalue()

    def test_aucun_modele_pret(self):
        with mock.patch.object(manager, "state", "idle"):
            code, out, err = self.call("-p", "Lis")
        self.assertEqual(code, cli.UNREACHABLE)
        self.assertIn("Aucun modèle", err)
        self.assertIn("n'en charge pas", err)

    def test_demande_vide_et_delai_invalide(self):
        self.assertEqual(self.call("-p", "   ")[0], cli.ERROR)
        code, out, err = self.call("--delai", "0", "-p", "Lis")
        self.assertEqual(code, cli.ERROR)
        self.assertIn("--delai", err)

    def test_erreur_en_json(self):
        with mock.patch.object(manager, "state", "idle"):
            code, out, err = self.call("--json", "-p", "Lis")
        self.assertEqual(json.loads(out)["code"], cli.UNREACHABLE)


if __name__ == "__main__":
    unittest.main()
