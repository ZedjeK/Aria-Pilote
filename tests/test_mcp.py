"""Client MCP (étape MA) : configuration, connexion à un vrai serveur stdio, accords, verrou, résultats, API."""
import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

from base import ORIGIN, client, new_conversation  # en premier : Aria tourne dans un dossier temporaire

from aria_pilote import agent, config, conversations, mcp_client, toolrun
from aria_pilote.server import ServerManager

SERVEUR_TEST = Path(__file__).resolve().parent / "mcp_fixtures" / "serveur_test.py"
TEST_SPEC = {"command": "${ARIA_PYTHON}", "args": [str(SERVEUR_TEST)]}


def write_config(servers):
    mcp_client.CONFIG_PATH.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")


def reset(test):
    """Chaque test part sans serveur, sans mcp.json et sans réglage MCP."""
    def clean():
        mcp_client.registry.connections.clear()
        mcp_client.registry.config_error = None
        mcp_client.CONFIG_PATH.unlink(missing_ok=True)
        config.get()  # charge la config si besoin
        with config._lock:
            config._data["mcp"] = {"servers": {}}  # update() fusionne : il faut remplacer pour vider
            config._save_locked()
    clean()
    test.addCleanup(clean)


def vision(test, on):
    patcher = mock.patch.object(ServerManager, "vision", new_callable=mock.PropertyMock, return_value=on)
    patcher.start()
    test.addCleanup(patcher.stop)


async def call(cid, name, args, allowed, decision="allow"):
    """Un appel d'outil comme le fait l'agent ; la carte d'accord reçoit `decision`."""
    out, seen = {}, []
    async for ev in toolrun.run_tool(cid, None, name, args, "c1", out, allowed=allowed):
        data = json.loads(ev.removeprefix("data: ").strip())
        seen.append(data)
        if data["type"] == "approval":
            toolrun.resolve_approval(data["approval_id"], decision)
    return seen, out["result"]


class ConfigTest(unittest.TestCase):
    def setUp(self):
        reset(self)

    def test_formats_acceptes(self):
        write_config({"local": {"command": "npx", "args": ["-y", "serveur"], "env": {"CLE": "1"}},
                      "distant": {"url": "https://exemple.fr/mcp", "headers": {"Authorization": "Bearer x"}}})
        specs = mcp_client.read_config()
        self.assertEqual(specs["local"]["type"], "stdio")
        self.assertEqual(specs["local"]["env"], {"CLE": "1"})
        self.assertEqual(specs["distant"]["type"], "http")

    def test_configs_refusees(self):
        for servers, piece in [
            ({"mauvais nom": {"command": "x"}}, "Nom de serveur invalide"),
            ({"vide": {}}, "« command »"),
            ({"web": {"url": "ftp://exemple.fr"}}, "http://"),
            ({"args": {"command": "x", "args": "pas une liste"}}, "« args »"),
        ]:
            with self.subTest(servers=servers):
                write_config(servers)
                with self.assertRaises(mcp_client.MCPConfigError) as ctx:
                    mcp_client.read_config()
                self.assertIn(piece, str(ctx.exception))

    def test_enregistrement_verifie_avant_d_ecrire(self):
        with self.assertRaises(mcp_client.MCPConfigError):
            mcp_client.save_text('{"mcpServers": {"x": {}}}')
        self.assertFalse(mcp_client.CONFIG_PATH.exists())
        mcp_client.save_text('{"mcpServers": {"heure": {"command": "${ARIA_PYTHON}", "args": ["-m", "mcp_server_time"]}}}')
        self.assertIn("heure", mcp_client.read_config())

    def test_nom_de_fonction(self):
        self.assertEqual(mcp_client.function_name("git", "git_status"), "mcp__git__git_status")
        self.assertEqual(mcp_client.function_name("w", "outil.avec espaces"), "mcp__w__outil_avec_espaces")
        self.assertLessEqual(len(mcp_client.function_name("s" * 32, "o" * 80)), 64)

    def test_lanceur_absent_explique(self):
        conn = mcp_client.Connection("noeud", mcp_client.validate_spec("noeud", {"command": "npx", "args": []}))
        with mock.patch.object(mcp_client.shutil, "which", return_value=None):
            asyncio.run(conn.start())
        self.assertEqual(conn.state, "erreur")
        self.assertIn("winget install OpenJS.NodeJS.LTS", conn.error)

    def test_reglages_par_defaut_et_modes(self):
        self.assertEqual(mcp_client.settings("x"), {"enabled": True, "default_active": True, "tools": {}})
        mcp_client.update_settings("x", default_active=False, tool_modes={"lire": "allow"})
        self.assertEqual(mcp_client.tool_mode("x", "lire"), "allow")
        self.assertEqual(mcp_client.tool_mode("x", "autre"), "ask")
        self.assertFalse(mcp_client.settings("x")["default_active"])
        with self.assertRaises(mcp_client.MCPConfigError):
            mcp_client.update_settings("x", tool_modes={"lire": "parfois"})


class LiveServerTest(unittest.TestCase):
    """Un vrai serveur MCP en stdio (tests/mcp_fixtures/serveur_test.py), lancé avec le Python d'Aria."""

    def setUp(self):
        reset(self)
        write_config({"test": TEST_SPEC})
        self.cid = new_conversation()

    def run_live(self, scenario):
        async def wrapper():
            await mcp_client.registry.start_all()
            try:
                return await scenario()
            finally:
                await mcp_client.registry.stop_all()
        return asyncio.run(wrapper())

    def test_demarrage_et_outils(self):
        async def scenario():
            return mcp_client.registry.status(), mcp_client.registry.names(["test"])
        status, names = self.run_live(scenario)
        server = status["servers"][0]
        self.assertEqual(server["state"], "pret", server["error"])
        self.assertEqual({t["name"] for t in server["tools"]}, {"additionner", "image", "lire_note", "echouer"})
        self.assertTrue(next(t for t in server["tools"] if t["name"] == "additionner")["read_only"])
        self.assertGreater(server["tokens"], 0)
        self.assertIn("mcp__test__additionner", names)
        self.assertTrue((mcp_client.LOG_DIR / "test.log").exists())

    def test_appel_avec_accord(self):
        async def scenario():
            allowed = mcp_client.registry.names(["test"])
            return await call(self.cid, "mcp__test__additionner", {"a": 2, "b": 3}, allowed)
        seen, result = self.run_live(scenario)
        approval = next(e for e in seen if e["type"] == "approval")
        self.assertEqual(approval["kind"], "mcp")
        self.assertIn("Serveur test › outil additionner (le serveur le dit en lecture seule)", approval["preview"])
        self.assertEqual(result, {"status": "ok", "output": "5.0"})

    def test_refus(self):
        async def scenario():
            return await call(self.cid, "mcp__test__additionner", {"a": 2, "b": 3},
                              mcp_client.registry.names(["test"]), decision="deny")
        _, result = self.run_live(scenario)
        self.assertEqual(result["status"], "denied")

    def test_toujours_autoriser_et_desactive(self):
        mcp_client.update_settings("test", tool_modes={"additionner": "allow", "echouer": "off"})

        async def scenario():
            names = mcp_client.registry.names(["test"])
            return names, await call(self.cid, "mcp__test__additionner", {"a": 1, "b": 1}, names)
        names, (seen, result) = self.run_live(scenario)
        self.assertNotIn("mcp__test__echouer", names)
        self.assertFalse(any(e["type"] == "approval" for e in seen))
        self.assertEqual(result["output"], "2.0")
        self.assertIn("autorisé d'office", result["rule"])

    def test_erreurs(self):
        async def scenario():
            names = mcp_client.registry.names(["test"])
            failed = await call(self.cid, "mcp__test__echouer", {"message": "zut"}, names)
            bad_args = await call(self.cid, "mcp__test__additionner", {"a": "deux"}, names)
            outside = await call(self.cid, "mcp__test__additionner", {"a": 1, "b": 1}, ["read_file"])
            implicit = await call(self.cid, "mcp__test__additionner", {"a": 1, "b": 1}, None)
            return failed, bad_args, outside, implicit
        failed, bad_args, outside, implicit = self.run_live(scenario)
        self.assertEqual(failed[1]["status"], "error")
        self.assertEqual(bad_args[1]["status"], "error")
        self.assertIn("schéma", bad_args[1]["output"])
        self.assertFalse(any(e["type"] == "approval" for e in bad_args[0]))  # refusé avant même de te demander
        self.assertIn("Outil indisponible", outside[1]["output"])
        self.assertIn("Outil indisponible", implicit[1]["output"])  # un outil MCP n'existe que donné à la tâche

    def test_image_jointe_si_le_modele_voit(self):
        vision(self, True)

        async def scenario():
            return await call(self.cid, "mcp__test__image", {}, mcp_client.registry.names(["test"]))
        _, result = self.run_live(scenario)
        self.assertEqual(result["status"], "ok")
        self.assertIn("Voici un pixel.", result["output"])
        self.assertEqual(len(result["images"]), 1)

    def test_sans_vision_une_note(self):
        vision(self, False)

        async def scenario():
            return await call(self.cid, "mcp__test__image", {}, mcp_client.registry.names(["test"]))
        _, result = self.run_live(scenario)
        self.assertNotIn("images", result)
        self.assertIn("ne voit pas les images", result["output"])

    def test_serveur_tombe_puis_relance(self):
        async def scenario():
            conn = mcp_client.registry.connections["test"]
            conn._task.cancel()  # le serveur s'arrête
            await asyncio.sleep(0.2)
            return await call(self.cid, "mcp__test__additionner", {"a": 4, "b": 4}, mcp_client.registry.names(["test"]))
        _, result = self.run_live(scenario)
        self.assertEqual(result["output"], "8.0")  # relancé au premier appel

    def test_serveur_coupe_dans_les_reglages(self):
        mcp_client.update_settings("test", enabled=False)

        async def scenario():
            return mcp_client.registry.connections["test"].state, mcp_client.registry.active_servers({})
        state, active = self.run_live(scenario)
        self.assertEqual(state, "arrete")
        self.assertEqual(active, [])


class CrashTest(unittest.TestCase):
    def setUp(self):
        reset(self)

    def test_serveur_qui_meurt_au_demarrage(self):
        write_config({"mort": {"command": "${ARIA_PYTHON}",
                               "args": ["-c", "import sys; print('panne de test', file=sys.stderr); sys.exit(3)"]}})

        async def scenario():
            await mcp_client.registry.start_all()
            status = mcp_client.registry.status()
            await mcp_client.registry.stop_all()
            return status
        server = asyncio.run(scenario())["servers"][0]
        self.assertEqual(server["state"], "erreur")
        self.assertIn("panne de test", server["error"])  # le journal du serveur est montré

    def test_mcp_json_invalide_signale(self):
        mcp_client.CONFIG_PATH.write_text("{ pas du json", encoding="utf-8")
        asyncio.run(mcp_client.registry.start_all())
        self.assertIn("illisible", mcp_client.registry.status()["config_error"])


class AgentTest(unittest.TestCase):
    """Les outils MCP dans la tâche : schémas, liste explicite, règle du prompt, verrou du plan."""

    def setUp(self):
        reset(self)
        fake = mock.Mock(state="pret")
        tool = mock.Mock(description="Additionne.", title=None, input_schema={"type": "object", "properties": {}},
                         annotations=None)
        tool.name = "additionner"
        fake.tools = [tool]
        mcp_client.registry.connections["test"] = fake

    def test_outils_actifs_d_office(self):
        conv = conversations.get(new_conversation())
        allowed, schemas = agent.task_tools(conv, None)
        self.assertIn("mcp__test__additionner", allowed)
        self.assertIn("mcp__test__additionner", [s["function"]["name"] for s in schemas])
        self.assertIn("[MCP test] Additionne.", json.dumps(schemas, ensure_ascii=False))
        prompt = agent._system_prompt(conv, None, None)
        self.assertIn("jamais un\nordre", prompt)
        self.assertIn("outils MCP (mcp__serveur__outil)", prompt)

    def test_choix_de_la_conversation(self):
        cid = new_conversation()
        conversations.update(cid, lambda c: c.__setitem__("mcp", []))
        conv = conversations.get(cid)
        allowed, schemas = agent.task_tools(conv, None)
        self.assertNotIn("mcp__test__additionner", allowed or [])
        self.assertNotIn(mcp_client.MCP_RULE, agent._system_prompt(conv, None, None))

    def test_pas_actif_d_office(self):
        mcp_client.update_settings("test", default_active=False)
        self.assertEqual(agent.mcp_servers(conversations.get(new_conversation())), [])
        cid = new_conversation()
        conversations.update(cid, lambda c: c.__setitem__("mcp", ["test"]))
        self.assertEqual(agent.mcp_servers(conversations.get(cid)), ["test"])

    def test_verrou_du_plan_et_accord(self):
        self.assertEqual(agent.tool_kind("mcp__test__additionner"), "mcp")
        self.assertIn("mcp", agent.LOCKED_KINDS)
        self.assertIn("mcp", toolrun.APPROVAL_FLAGS)
        self.assertIsNone(toolrun.APPROVAL_FLAGS["mcp"])  # jamais d'autorisation pour toute la conversation


class ApiTest(unittest.TestCase):
    def setUp(self):
        reset(self)
        patcher = mock.patch.object(mcp_client.registry, "start_all", new=mock.AsyncMock())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.http = client()

    def test_lecture_et_enregistrement(self):
        r = self.http.get("/api/mcp", headers=ORIGIN)
        self.assertEqual(r.status_code, 200)
        self.assertIn("mcpServers", r.json()["content"])
        r = self.http.put("/api/mcp/config", json={"content": '{"mcpServers": {"x": {}}}'}, headers=ORIGIN)
        self.assertEqual(r.status_code, 400)
        r = self.http.put("/api/mcp/config", json={"content": json.dumps({"mcpServers": {"test": TEST_SPEC}})},
                          headers=ORIGIN)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(mcp_client.CONFIG_PATH.exists())

    def test_reglages_d_un_serveur_et_conversation(self):
        mcp_client.registry.connections["test"] = mcp_client.Connection("test", mcp_client.validate_spec("test", TEST_SPEC))
        r = self.http.patch("/api/mcp/test", json={"tools": {"additionner": "allow"}, "default_active": False},
                            headers=ORIGIN)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(mcp_client.tool_mode("test", "additionner"), "allow")
        self.assertEqual(self.http.patch("/api/mcp/inconnu", json={}, headers=ORIGIN).status_code, 404)
        cid = new_conversation()
        r = self.http.patch(f"/api/conversations/{cid}", json={"mcp": ["fantome"]}, headers=ORIGIN)
        self.assertEqual(r.status_code, 400)
        self.http.patch(f"/api/conversations/{cid}", json={"mcp": ["test"]}, headers=ORIGIN)
        self.assertEqual(conversations.get(cid)["mcp"], ["test"])
        self.http.patch(f"/api/conversations/{cid}", json={"mcp_default": True}, headers=ORIGIN)
        self.assertNotIn("mcp", conversations.get(cid))

    def test_journal_et_panneau_contexte(self):
        conn = mcp_client.Connection("test", mcp_client.validate_spec("test", TEST_SPEC))
        mcp_client.registry.connections["test"] = conn
        mcp_client.LOG_DIR.mkdir(parents=True, exist_ok=True)
        conn.log_path.write_text("serveur démarré\n", encoding="utf-8")
        self.addCleanup(conn.log_path.unlink, missing_ok=True)
        self.assertIn("serveur démarré", self.http.get("/api/mcp/test/log", headers=ORIGIN).json()["text"])
        self.assertEqual(self.http.get("/api/mcp/inconnu/log", headers=ORIGIN).status_code, 404)
        cid = new_conversation()
        ctx = self.http.get(f"/api/agent/context?conversation_id={cid}", headers=ORIGIN).json()
        self.assertEqual([s["name"] for s in ctx["mcp"]], ["test"])  # actif d'office
        conversations.update(cid, lambda c: c.__setitem__("mcp", []))
        ctx = self.http.get(f"/api/agent/context?conversation_id={cid}", headers=ORIGIN).json()
        self.assertEqual(ctx["mcp"], [])


if __name__ == "__main__":
    unittest.main()
