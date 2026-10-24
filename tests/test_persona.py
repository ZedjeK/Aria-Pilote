"""Personas, fiche « Toi », assemblage du prompt, registre par compétence, migrations (presets, MEMOIRE.md)."""

import asyncio
import copy
import json
import shutil
import unittest

from base import ORIGIN, client, events, new_conversation, workspace

from aria_pilote import agent, chat, config, conversations, persona, presets


def keep_state(test):
    """Config, presets, personas et fiche « Toi » remis comme avant le test."""
    saved_config = copy.deepcopy(config.get())
    saved_presets = copy.deepcopy(presets.list_all())
    saved_dir = persona.DATA_DIR.with_name("personas-sauvegarde-test")
    if saved_dir.exists():
        shutil.rmtree(saved_dir)
    if persona.DATA_DIR.exists():
        shutil.copytree(persona.DATA_DIR, saved_dir)
    saved_profile = persona.PROFILE_PATH.read_text(encoding="utf-8") if persona.PROFILE_PATH.exists() else None

    def restore():
        with config._lock:
            config._data = saved_config
            config._save_locked()
        with presets._lock:
            presets._write(saved_presets)
        shutil.rmtree(persona.DATA_DIR, ignore_errors=True)
        if saved_dir.exists():
            shutil.copytree(saved_dir, persona.DATA_DIR)
            shutil.rmtree(saved_dir)
        if saved_profile is None:
            persona.PROFILE_PATH.unlink(missing_ok=True)
        else:
            persona.PROFILE_PATH.write_text(saved_profile, encoding="utf-8")
    test.addCleanup(restore)


NOVA = {"name": "Nova", "avatar": "🛰", "gender": "feminin", "presentation": "Une assistante discrète et précise.",
        "traits": {"chaleur": {"naturel": 3, "sobre": 1, "professionnel": 0},
                   "humour-geek": {"naturel": 2, "sobre": 0, "professionnel": 0},
                   "gouts": {"naturel": 1, "sobre": 0, "professionnel": 0}},
        "themes": ["espace", "science-fiction"], "rules": ["Ne jamais inventer un fait."], "intimate": False,
        "registers": {"discussion": "naturel", "revue-code": "professionnel"}}


class Catalogue(unittest.TestCase):
    def test_identifiants_et_intensites(self):
        ids = [t["id"] for t in persona.CATALOGUE["traits"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), 62)
        families = {f["id"] for f in persona.CATALOGUE["families"]}
        for trait in persona.CATALOGUE["traits"]:
            self.assertIn(trait["family"], families)
            self.assertEqual(set(trait["suggested"]), set(persona.REGISTERS))
            self.assertTrue(all(0 <= v <= 3 for v in trait["suggested"].values()))
        self.assertIn("gouts", persona.TRAITS)


class Personas(unittest.TestCase):
    def setUp(self):
        keep_state(self)

    def test_persona_livre_neutre(self):
        aria = persona.get("aria")
        self.assertIsNotNone(aria)
        self.assertFalse(aria["intimate"])
        self.assertNotIn("Jérémy", json.dumps(aria, ensure_ascii=False))

    def test_seule_la_colonne_du_registre_est_lue(self):
        nova = persona.create(NOVA)
        naturel, pro = persona.render(nova, "naturel"), persona.render(nova, "professionnel")
        self.assertIn("Chaleur (intensité très marquée)", naturel)
        self.assertIn("Humour geek", naturel)
        self.assertIn("Espace et astronomie", naturel)  # goûts présents en naturel
        self.assertNotIn("Chaleur", pro)  # trait à 0 : absent, pas « ne l'utilise pas »
        self.assertNotIn("Espace", pro)
        self.assertIn("Ne jamais inventer un fait.", pro)  # les règles d'or restent dans tous les registres
        self.assertIn("non autorisé", pro)

    def test_validation_et_import_tolerant(self):
        warnings = []
        created = persona.create({"name": "X", "traits": {"chaleur": {"naturel": 9}, "inconnu": {}},
                                  "themes": ["espace", "zzz"], "rules": ["  a  ", ""] + ["r"] * 30}, warnings)
        self.assertEqual(created["traits"]["chaleur"]["naturel"], 3)  # borné
        self.assertEqual(created["themes"], ["espace"])
        self.assertEqual(len(created["rules"]), persona.MAX_RULES)
        self.assertEqual(len(warnings), 2)
        with self.assertRaises(persona.PersonaError):
            persona.create({"name": ""})

    def test_export_import(self):
        nova = persona.create(NOVA)
        data = persona.export(nova["id"])
        self.assertEqual((data["type"], data["format"]), ("aria-pilote-persona", persona.FORMAT))
        imported, warnings = persona.import_(data)
        self.assertNotEqual(imported["id"], nova["id"])  # une copie, pas un écrasement
        self.assertEqual(imported["traits"], nova["traits"])
        with self.assertRaises(persona.PersonaError):
            persona.import_({"name": "Pas un persona"})
        with self.assertRaises(persona.PersonaError):
            persona.import_({**data, "format": persona.FORMAT + 1})

    def test_suppression(self):
        nova = persona.create(NOVA)
        config.update({"default_persona": nova["id"]})
        persona.delete(nova["id"])
        self.assertEqual(config.get()["default_persona"], persona.list_all()[0]["id"])
        for p in persona.list_all()[1:]:
            persona.delete(p["id"])
        with self.assertRaises(persona.PersonaError):
            persona.delete(persona.list_all()[0]["id"])  # le dernier reste

    def test_registre_selon_la_competence(self):
        nova = persona.create(NOVA)
        self.assertEqual(persona.register_for(nova), "naturel")
        self.assertEqual(persona.register_for(nova, "revue-code"), "professionnel")
        self.assertEqual(persona.register_for(nova, "nouvelle-competence"), "sobre")  # par défaut
        note = persona.register_note(nova, "revue-code")
        self.assertIn("registre professionnel", note)
        self.assertIn("reste neutre", note)  # aucun trait à ce registre
        self.assertEqual(persona.register_note(None, "revue-code"), "")


class Assembly(unittest.TestCase):
    def setUp(self):
        keep_state(self)
        self.nova = persona.create(NOVA)
        persona.save_profile({"name": "Jérémy", "gender": "masculin", "job": "Admin système", "in_folder": False})

    def prompt(self, conv, root=None):
        return agent._system_prompt(conv, root, None)

    def test_ordre_des_couches(self):
        text = self.prompt({"persona_id": self.nova["id"]})
        order = [text.index(s) for s in ("tu es Nova", "Qui est l'utilisateur", "Outils disponibles",
                                         "Ordre de priorité")]
        self.assertEqual(order, sorted(order))
        self.assertIn("Prénom : Jérémy (accord au masculin)", text)

    def test_sans_persona_et_fiche_hors_dossier(self):
        self.assertNotIn("Qui tu es", self.prompt({"persona_id": "none"}))
        root = str(workspace())
        with_folder = self.prompt({"persona_id": self.nova["id"]}, root)
        self.assertNotIn("Qui est l'utilisateur", with_folder)  # « aussi quand un dossier est attaché » coupé
        self.assertIn("tu es Nova", with_folder)

    def test_persona_par_defaut_et_supprime(self):
        config.update({"default_persona": self.nova["id"]})
        self.assertEqual(persona.for_conversation({})["id"], self.nova["id"])
        self.assertEqual(persona.for_conversation({"persona_id": "efface"})["id"], self.nova["id"])

    def test_registre_ajoute_a_la_competence_chargee(self):
        root = workspace()
        cid = new_conversation(root)
        conversations.update(cid, lambda c: c.update(persona_id=self.nova["id"], task_id="t1"))
        ctx = {"preset": {}, "model": "x", "allowed": None, "seen": {}, "stuck": False, "planned": False,
               "asked": False, "task_id": "t1"}
        call = {"id": "c1", "function": {"name": "load_skill", "arguments": json.dumps({"name": "revue-code"})}}
        out = events(asyncio.run(_collect(agent._execute(cid, str(root), call, ctx))))
        result = next(e for e in out if e["type"] == "tool_result")
        self.assertIn("registre professionnel", result["output"])
        # « /revue-code … » tapé dans le chat : même registre ajouté au message
        conv = conversations.get(cid)
        conv["messages"].append({"role": "user", "content": "relis ça", "skill": "revue-code"})
        messages = chat.build_messages(conv, "")
        self.assertIn("registre professionnel", messages[-1]["content"])


async def _collect(gen):
    return [e async for e in gen]


class Migrations(unittest.TestCase):
    def setUp(self):
        keep_state(self)

    def test_prompts_des_presets(self):
        with presets._lock:
            presets._write([{"id": "default", "name": "Par défaut", "system_prompt": "Tu es une assistante utile. Réponds en français."},
                            {"id": "pirate", "name": "Pirate", "system_prompt": "Réponds comme un pirate."}])
        items = presets.list_all()
        self.assertTrue(all("system_prompt" not in p for p in items))
        imported = [p for p in persona.list_all() if p["name"].startswith("Pirate (prompt importé)")]
        self.assertEqual(len(imported), 1)  # le prompt écrit à la main n'est pas perdu
        self.assertEqual(imported[0]["presentation"], "Réponds comme un pirate.")
        self.assertFalse(any("assistante utile" in p.get("presentation", "") for p in persona.list_all()))

    def test_memoire_du_projet(self):
        root = workspace()
        (root / "ARIA.md").write_text("ancien", encoding="utf-8")
        self.assertEqual((agent.memory_file(str(root)), agent.load_memory(str(root))), ("ARIA.md", "ancien"))
        (root / "MEMOIRE.md").write_text("nouveau", encoding="utf-8")
        self.assertEqual(agent.load_memory(str(root)), "nouveau")  # MEMOIRE.md prioritaire

    def test_renommer_en_memoire(self):
        api = client()
        root = workspace()
        cid = new_conversation(root)
        (root / "ARIA.md").write_text("consignes", encoding="utf-8")
        resp = api.post("/api/agent/memory/rename", json={"conversation_id": cid}, headers=ORIGIN)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual((root / "MEMOIRE.md").read_text(encoding="utf-8"), "consignes")
        self.assertFalse((root / "ARIA.md").exists())
        self.assertEqual(api.post("/api/agent/memory/rename", json={"conversation_id": cid}, headers=ORIGIN).status_code, 404)


class Api(unittest.TestCase):
    def setUp(self):
        keep_state(self)
        self.api = client()

    def test_persona_de_la_conversation(self):
        nova = persona.create(NOVA)
        conv = self.api.post("/api/conversations", json={"persona_id": nova["id"]}, headers=ORIGIN).json()
        self.assertEqual(conv["persona_id"], nova["id"])
        self.assertEqual(self.api.post("/api/conversations", json={"persona_id": "fantome"}, headers=ORIGIN).status_code, 400)
        patched = self.api.patch(f"/api/conversations/{conv['id']}", json={"persona_id": "none"}, headers=ORIGIN).json()
        self.assertEqual(patched["persona_id"], "none")
        ctx = self.api.get(f"/api/agent/context?conversation_id={conv['id']}").json()
        self.assertIsNone(ctx["persona"])
        self.assertEqual(ctx["layers"]["persona"], 0)

    def test_fiche_toi(self):
        resp = self.api.put("/api/profile", json={"name": "Jérémy", "address": "vous", "language": "en"}, headers=ORIGIN)
        self.assertIn("Vouvoie l'utilisateur et réponds en anglais.", resp.json()["preview"])
        self.assertEqual(self.api.put("/api/profile", json={"address": "toi"}, headers=ORIGIN).status_code, 400)

    def test_export_par_l_api(self):
        resp = self.api.get("/api/personas/aria/export")
        self.assertIn("attachment", resp.headers["content-disposition"])
        imported = self.api.post("/api/personas/import", json=resp.json(), headers=ORIGIN).json()
        self.assertEqual(imported["persona"]["name"], "Aria")
        preview = self.api.get("/api/personas/aria/preview?register=professionnel").json()
        self.assertIn("registre professionnel", preview["persona"])


if __name__ == "__main__":
    unittest.main()
