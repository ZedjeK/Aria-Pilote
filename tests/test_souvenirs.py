"""Souvenirs (outil « retenir ») : garde-fous, doublons, intime, budget du prompt, conversation privée, outils et API."""

import asyncio
import copy
import json
import unittest

from base import ORIGIN, client, events, new_conversation, workspace

from aria_pilote import agent, config, conversations, persona, souvenirs, tools


def keep_config(test):
    saved = copy.deepcopy(config.get())

    def restore():
        with config._lock:
            config._data = saved
            config._save_locked()
    test.addCleanup(restore)


def run_ctx():
    return {"preset": {}, "model": "x", "allowed": None, "seen": {}, "stuck": False,
            "planned": False, "asked": False, "task_id": "t1"}


async def execute(cid, name, args, root=None):
    call = {"id": "c1", "function": {"name": name, "arguments": json.dumps(args)}}
    out = events([e async for e in agent._execute(cid, str(root) if root else None, call, run_ctx())])
    return next(e for e in out if e["type"] == "tool_result")


class Base(unittest.TestCase):
    def setUp(self):
        keep_config(self)
        souvenirs.clear()
        self.addCleanup(souvenirs.clear)


class Secrets(unittest.TestCase):
    def test_secrets_refuses(self):
        for text in ("Son mot de passe est Hunter2024", "mdp: azerty123", "sa clé sk-abcdefghijklmnop1234",
                     "jeton ghp_abcdefghijklmnopqrstu", "IBAN FR76 3000 6000 0112 3456 7890 189",
                     "carte 4970 1012 3456 7890 2"):
            with self.subTest(text=text):
                self.assertIsNotNone(souvenirs.secret_in(text))

    def test_phrases_ordinaires_permises(self):
        for text in ("Il change de mot de passe tous les mois", "Il aime le code pin-up des années 50",
                     "Son PC a 64 Go de RAM et une RTX 4090", "Il est né en 1993"):
            with self.subTest(text=text):
                self.assertIsNone(souvenirs.secret_in(text))

    def test_carte_sans_luhn_permise(self):
        self.assertIsNone(souvenirs.secret_in("numéro de série 1234 5678 9012 3456"))


class Stockage(Base):
    def test_doublon_mis_a_jour(self):
        first, old = souvenirs.add("Jérémy aime le café noir", "preferences")
        self.assertIsNone(old)
        second, old = souvenirs.add("Jérémy aime le café noir sans sucre", "preferences")
        self.assertEqual((second["id"], old), (first["id"], "Jérémy aime le café noir"))
        self.assertEqual(len(souvenirs.all_souvenirs()), 1)
        # Même texte, autre catégorie : un autre souvenir
        souvenirs.add("Jérémy aime le café noir", "toi")
        self.assertEqual(len(souvenirs.all_souvenirs()), 2)

    def test_remplace_designe(self):
        first, _ = souvenirs.add("Il travaille sur Aria Pilote", "projets")
        saved, old = souvenirs.add("Il a fini la version 0.21 d'Aria", "projets", remplace=first["id"])
        self.assertEqual((saved["id"], old), (first["id"], "Il travaille sur Aria Pilote"))
        with self.assertRaises(souvenirs.SouvenirError):
            souvenirs.add("Autre chose", "projets", remplace="inconnu")

    def test_vide_trop_long_ou_secret(self):
        for text in ("   ", "x" * (souvenirs.MAX_CHARS + 1), "mdp = azerty123"):
            with self.subTest(text=text[:20]), self.assertRaises(souvenirs.SouvenirError):
                souvenirs.add(text)
        self.assertEqual(souvenirs.all_souvenirs(), [])

    def test_modifier_supprimer_tout_oublier(self):
        saved, _ = souvenirs.add("Il joue à Warhammer 40k", "toi")
        self.assertEqual(souvenirs.update(saved["id"], categorie="preferences")["categorie"], "preferences")
        with self.assertRaises(souvenirs.SouvenirError):
            souvenirs.update(saved["id"], categorie="nimporte")
        self.assertEqual(souvenirs.remove(saved["id"])["id"], saved["id"])
        with self.assertRaises(souvenirs.SouvenirError):
            souvenirs.remove(saved["id"])
        souvenirs.add("Un", "toi")
        souvenirs.add("Deux trois", "projets")
        self.assertEqual(souvenirs.clear(), 2)


class Intime(Base):
    def setUp(self):
        super().setUp()
        self.intime = {"id": "p1", "intimate": True}
        souvenirs.add("Il aime le café du matin", "preferences")
        souvenirs.add("Il aime les câlins le soir", "relation", persona="p1", intime=True)

    def textes(self, found):
        return {s["texte"] for s in found}

    def test_seulement_le_bon_persona_en_naturel(self):
        self.assertEqual(len(souvenirs.search("aime", self.intime)), 2)
        for who, register in ((self.intime, "pro"), ({"id": "p2", "intimate": True}, "naturel"),
                              ({"id": "p1", "intimate": False}, "naturel"), (None, "naturel")):
            with self.subTest(who=who, register=register):
                self.assertEqual(self.textes(souvenirs.search("aime", who, register)), {"Il aime le café du matin"})

    def test_jamais_dans_une_tache_avec_dossier(self):
        self.assertEqual(len(souvenirs.for_prompt("aime", self.intime)), 2)
        self.assertEqual(self.textes(souvenirs.for_prompt("aime", self.intime, folder=True)),
                         {"Il aime le café du matin"})

    def test_souvenir_commun_sans_persona(self):
        common = next(s for s in souvenirs.all_souvenirs() if not s["intime"])
        self.assertIsNone(common["persona"])


class Budget(Base):
    def test_plus_proches_puis_plus_recents(self):
        for text in ("Il aime le thé vert", "Il joue à NieR Automata", "Il vit en Bretagne", "Il a un chat roux",
                     "Il court le dimanche"):
            souvenirs.add(text, "toi")
        souvenirs.add("Il monte son PC lui-même", "toi")
        config.update({"memory": {"max_injected": 3}})
        chosen = souvenirs.for_prompt("mon PC chauffe")
        self.assertEqual(len(chosen), 3)
        self.assertEqual(chosen[0]["texte"], "Il monte son PC lui-même")
        self.assertEqual(chosen[1]["texte"], "Il court le dimanche")  # puis les plus récents
        config.update({"memory": {"max_injected": 0}})
        self.assertEqual(souvenirs.for_prompt("PC"), [])

    def test_budget_en_caracteres(self):
        for i in range(40):
            souvenirs.add(f"{i} " + " ".join(f"mot{i}x{j}" for j in range(30)), "toi")  # ≈ 250 caractères chacun
        chosen = souvenirs.for_prompt("")
        self.assertLess(len(chosen), 40)
        self.assertLessEqual(sum(len(s["texte"]) + 12 for s in chosen), souvenirs.MAX_PROMPT_CHARS)

    def test_rendu(self):
        self.assertIn("aucun souvenir", souvenirs.render([], 0))
        saved, _ = souvenirs.add("Il préfère quatre espaces", "preferences")
        text = souvenirs.render([saved], 7)
        self.assertIn(f"- [{saved['id']}] Préférences · Il préfère quatre espaces (", text)
        self.assertIn("1 sur 7", text)


class Agent(Base):
    def test_outils_et_prompt_selon_le_reglage_et_le_mode_prive(self):
        souvenirs.add("Il adore Blade Runner", "preferences")
        cid = new_conversation()
        conv = conversations.get(cid)
        allowed, schemas = agent.task_tools(conv, None)
        self.assertTrue(agent.MEMORY_TOOLS <= set(allowed))
        self.assertIn("Il adore Blade Runner", agent._system_prompt(conv, None, None))
        conversations.update(cid, lambda c: c.__setitem__("prive", True))
        conv = conversations.get(cid)
        allowed, schemas = agent.task_tools(conv, None)
        self.assertFalse(agent.MEMORY_TOOLS & set(allowed))
        self.assertFalse(agent.MEMORY_TOOLS & {s["function"]["name"] for s in schemas})
        self.assertNotIn("Blade Runner", agent._system_prompt(conv, None, None))
        # Coupés dans les Réglages : pareil, et même avec un dossier (liste « tous les outils »)
        config.update({"memory": {"enabled": False}})
        allowed, _ = agent.task_tools(conversations.get(new_conversation(workspace())), "x")
        self.assertFalse(agent.MEMORY_TOOLS & set(allowed))
        self.assertIn("read_file", allowed)

    def test_retenir_puis_oublier(self):
        cid = new_conversation()
        result = asyncio.run(execute(cid, "retenir", {"texte": "Il a une Captur hybride", "categorie": "toi"}))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["souvenir"]["action"], "ajouté")
        self.assertIsNone(result.get("rule"))
        saved = souvenirs.all_souvenirs()[0]
        self.assertEqual((saved["conversation"], saved["intime"]), (cid, False))
        message = next(m for m in conversations.get(cid)["messages"] if m.get("souvenir"))
        self.assertEqual(message["souvenir"]["id"], saved["id"])
        result = asyncio.run(execute(cid, "retenir", {"texte": "Il a une Captur hybride grise", "categorie": "toi"}))
        self.assertEqual((result["souvenir"]["action"], result["souvenir"]["ancien"]),
                         ("mis à jour", "Il a une Captur hybride"))
        result = asyncio.run(execute(cid, "oublier", {"texte": "Captur hybride"}))
        self.assertEqual(result["souvenir"]["action"], "oublié")
        self.assertEqual(souvenirs.all_souvenirs(), [])

    def test_refus(self):
        cid = new_conversation()
        result = asyncio.run(execute(cid, "retenir", {"texte": "mot de passe : Hunter2024", "categorie": "toi"}))
        self.assertEqual(result["status"], "error")
        self.assertIn("Je ne retiens jamais", result["output"])
        result = asyncio.run(execute(cid, "retenir", {"texte": "Il aime ça", "categorie": "secrets"}))
        self.assertEqual(result["status"], "error")
        # Intime avec un persona qui ne l'autorise pas
        sage = persona.create({"name": "Sage", "intimate": False})
        conversations.update(cid, lambda c: c.__setitem__("persona_id", sage["id"]))
        result = asyncio.run(execute(cid, "retenir", {"texte": "Il aime les câlins", "categorie": "relation",
                                                      "intime": True}))
        self.assertEqual(result["status"], "error")
        self.assertIn("intime", result["output"])
        # Conversation privée : l'outil n'est même pas donné au modèle
        conversations.update(cid, lambda c: c.__setitem__("prive", True))
        result = asyncio.run(execute(cid, "retenir", {"texte": "Il aime le thé", "categorie": "toi"}))
        self.assertEqual(result["status"], "error")
        self.assertEqual(souvenirs.all_souvenirs(), [])

    def test_retenir_en_mode_prive_refuse_par_l_outil(self):
        cid = new_conversation()
        conversations.update(cid, lambda c: c.__setitem__("prive", True))
        prepared = tools.TOOLS["retenir"].prepare(None, {"texte": "Il aime le thé", "categorie": "toi"})
        with self.assertRaises(tools.ToolError):
            asyncio.run(tools.TOOLS["retenir"].run(prepared, cid))

    def test_intime_rattache_au_persona(self):
        cid = new_conversation()
        coquine = persona.create({"name": "Coquine", "intimate": True})
        conversations.update(cid, lambda c: c.__setitem__("persona_id", coquine["id"]))
        result = asyncio.run(execute(cid, "retenir", {"texte": "Il aime les câlins", "categorie": "relation",
                                                      "intime": True}))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(souvenirs.all_souvenirs()[0]["persona"], coquine["id"])

    def test_me_demander_avant(self):
        args = {"texte": "Il aime le thé", "categorie": "toi"}
        self.assertTrue(tools.TOOLS["retenir"].prepare(None, args)["auto_allowed"])
        config.update({"memory": {"ask_before": True}})
        self.assertFalse(tools.TOOLS["retenir"].prepare(None, args)["auto_allowed"])

    def test_oublier_ambigu_et_chercher(self):
        a, _ = souvenirs.add("Il joue à Warhammer 40k", "preferences")
        souvenirs.add("Il peint des figurines Warhammer", "projets")
        cid = new_conversation()
        result = asyncio.run(execute(cid, "oublier", {"texte": "Warhammer"}))
        self.assertEqual(result["status"], "error")
        self.assertIn("Plusieurs souvenirs", result["output"])
        result = asyncio.run(execute(cid, "chercher_souvenirs", {"recherche": "figurines"}))
        self.assertIn("peint des figurines", result["output"])
        self.assertNotIn("40k", result["output"])
        result = asyncio.run(execute(cid, "oublier", {"id": a["id"]}))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(souvenirs.all_souvenirs()), 1)


class Format(Base):
    """« Sujet : fait au présent. » : coupé, rangé, daté, et le même sujet met à jour au lieu de doubler."""

    def test_decoupe_et_nettoyage(self):
        self.assertEqual(souvenirs.split("Voiture : Il roule en Captur"), ("il roule en Captur", "Voiture"))
        self.assertEqual(souvenirs.split("Voiture: il roule en Captur"), ("il roule en Captur", "Voiture"))
        self.assertEqual(souvenirs.split("Voiture : il roule en Captur", "voiture"), ("il roule en Captur", "Voiture"))
        self.assertEqual(souvenirs.split("il roule en Captur", "réponses :"), ("il roule en Captur", "Réponses"))
        # Pas un sujet : une heure, une phrase avec un pronom, quatre mots ou plus, un sujet différent déjà donné
        for text in ("Il se lève à 6:30", "Il a dit : bonjour", "Le soir quand il rentre : il joue",
                     "Note : il joue"):
            with self.subTest(text=text):
                self.assertEqual(souvenirs.split(text, "Jeux" if text.startswith("Note") else "")[0], text)
        self.assertEqual(souvenirs.split("Renault Captur hybride", "Voiture"), ("Renault Captur hybride", "Voiture"))

    def test_secret_coupe_en_deux_refuse(self):
        for args in (("mot de passe : Hunter2024", ""), ("Hunter2024", "mot de passe :")):
            with self.subTest(args=args), self.assertRaises(souvenirs.SouvenirError):
                souvenirs.add(*args[:1], sujet=args[1])
        saved, _ = souvenirs.add("il aime le thé", sujet="Boisson")
        with self.assertRaises(souvenirs.SouvenirError):
            souvenirs.update(saved["id"], sujet="mot de passe :", texte="Hunter2024")
        with self.assertRaises(souvenirs.SouvenirError):
            souvenirs.add("x", sujet="un sujet bien trop long pour tenir en trois mots")

    def test_meme_sujet_met_a_jour(self):
        first, _ = souvenirs.add("il roule en Captur hybride grise", "toi", sujet="Voiture")
        saved, old = souvenirs.add("sa Captur est blanche", "toi", sujet="voiture")
        self.assertEqual((saved["id"], old, saved["ancien_sujet"]), (first["id"], "il roule en Captur hybride grise",
                                                                     "Voiture"))
        self.assertNotIn("ancien_sujet", souvenirs.all_souvenirs()[0])
        # Même sujet, mais rien à voir : deux souvenirs
        souvenirs.add("il adore NieR Automata", "preferences", sujet="Jeux")
        souvenirs.add("il joue à Stellar Blade", "preferences", sujet="Jeux")
        self.assertEqual(len(souvenirs.all_souvenirs()), 3)

    def test_rendu_date_et_recherche_par_sujet(self):
        saved, _ = souvenirs.add("il les préfère courtes", "preferences", sujet="Réponses")
        text = souvenirs.render([saved], 1)
        self.assertIn(f"- [{saved['id']}] Préférences · Réponses : il les préfère courtes (", text)
        self.assertRegex(text, r"\((janv\.|févr\.|mars|avr\.|mai|juin|juil\.|août|sept\.|oct\.|nov\.|déc\.) \d{4}\)")
        self.assertIn("l'emportent en cas de désaccord", text)
        self.assertEqual([s["id"] for s in souvenirs.search("mes réponses")], [saved["id"]])
        old, _ = souvenirs.add("Il aime le café", "preferences")  # souvenir d'avant le format : sans sujet
        self.assertIn("Préférences · Il aime le café (", souvenirs.render([old], 2))

    def test_outil_retenir_avec_sujet(self):
        cid = new_conversation()
        result = asyncio.run(execute(new_conversation(), "retenir", {"sujet": "Voiture", "categorie": "toi",
                                                                     "texte": "Il roule en Captur hybride grise"}))
        self.assertEqual(result["status"], "ok")
        self.assertIn("Voiture : il roule en Captur hybride grise", result["output"])
        result = asyncio.run(execute(cid, "retenir", {"sujet": "Voiture", "texte": "il a une Captur blanche, pas grise",
                                                      "categorie": "toi"}))
        self.assertEqual(result["souvenir"]["action"], "mis à jour")
        self.assertEqual([(s["sujet"], s["texte"]) for s in souvenirs.all_souvenirs()],
                         [("Voiture", "il a une Captur blanche, pas grise")])
        # L'annulation remet le texte ET le sujet d'avant
        souvenirs.update(souvenirs.all_souvenirs()[0]["id"], sujet="Auto")
        api = client()
        api.post("/api/souvenirs/annuler", json={"conversation_id": cid, "tool_call_id": "c1"}, headers=ORIGIN)
        self.assertEqual([(s["sujet"], s["texte"]) for s in souvenirs.all_souvenirs()],
                         [("Voiture", "il roule en Captur hybride grise")])
        self.assertIn("sujet", tools.TOOLS["retenir"].parameters["required"])


class Fiche(Base):
    """Ce que dit déjà la fiche « Toi » : le modèle ne le recopie pas ; ce qui la contredit est gardé."""

    PROFILE = {"name": "Jérémy", "gender": "masculin", "job": "Admin système et réseau",
               "likes": "Des réponses courtes, de l'humour et des emojis",
               "about": "Il monte ses PC lui-même, joue à Warhammer 40k et roule en Captur hybride."}

    def setUp(self):
        super().setUp()
        self.addCleanup(persona.PROFILE_PATH.unlink, missing_ok=True)
        persona.save_profile(self.PROFILE)

    def test_doublons_de_la_fiche(self):
        for text in ("il s'appelle Jérémy", "Ses prénoms sont Jérémy", "il est admin système et réseau",
                     "il travaille comme admin réseau et système", "il aime les réponses courtes avec de l'humour",
                     "il joue à Warhammer 40k"):
            with self.subTest(text=text):
                self.assertIsNotNone(souvenirs.in_profile(text, self.PROFILE))

    def test_complete_ou_contredit_la_fiche(self):
        for text in ("il est passé chef de projet", "il roule en Captur hybride blanche depuis octobre",
                     "il a un chat", "il s'appelle Jérémy Dupont", "il préfère Bonsai à Qwen pour coder"):
            with self.subTest(text=text):
                self.assertIsNone(souvenirs.in_profile(text, self.PROFILE))
        self.assertIsNone(souvenirs.in_profile("il s'appelle Jérémy", {}))  # fiche vide : rien ne bloque

    def test_outil_refuse_le_doublon_garde_la_contradiction(self):
        cid = new_conversation()
        result = asyncio.run(execute(cid, "retenir", {"sujet": "Métier", "texte": "il est admin système et réseau",
                                                      "categorie": "toi"}))
        self.assertEqual(result["status"], "error")
        self.assertIn("Déjà dans la fiche de l'utilisateur (Métier)", result["output"])
        result = asyncio.run(execute(cid, "retenir", {"sujet": "Métier", "texte": "il est passé chef de projet",
                                                      "categorie": "toi"}))
        self.assertEqual(result["status"], "ok")
        self.assertEqual([s["texte"] for s in souvenirs.all_souvenirs()], ["il est passé chef de projet"])

    def test_ajout_a_la_main_signale_sans_bloquer(self):
        api = client()
        r = api.post("/api/souvenirs", json={"sujet": "Métier", "texte": "il est admin système et réseau"},
                     headers=ORIGIN)
        self.assertEqual((r.status_code, r.json()["dans_fiche"], r.json()["sujet"]), (200, "Métier", "Métier"))
        r = api.post("/api/souvenirs", json={"texte": "Voiture : il roule en Captur blanche", "categorie": "toi"},
                     headers=ORIGIN)
        self.assertEqual((r.json()["sujet"], r.json()["texte"], r.json()["dans_fiche"]),
                         ("Voiture", "il roule en Captur blanche", None))
        r = api.put(f"/api/souvenirs/{r.json()['id']}", json={"sujet": ""}, headers=ORIGIN)
        self.assertEqual(r.json()["sujet"], "")


class Api(Base):
    def setUp(self):
        super().setUp()
        self.api = client()

    def test_crud(self):
        r = self.api.post("/api/souvenirs", json={"texte": "Il aime la synthwave", "categorie": "preferences"},
                          headers=ORIGIN)
        self.assertEqual(r.status_code, 200)
        sid = r.json()["id"]
        self.assertEqual(self.api.post("/api/souvenirs", json={"texte": "mdp: azerty123"}, headers=ORIGIN).status_code,
                         400)
        listing = self.api.get("/api/souvenirs").json()
        self.assertEqual([s["id"] for s in listing["souvenirs"]], [sid])
        self.assertIn("preferences", listing["categories"])
        self.assertTrue(listing["settings"]["enabled"])
        r = self.api.put(f"/api/souvenirs/{sid}", json={"texte": "Il adore la synthwave"}, headers=ORIGIN)
        self.assertEqual(r.json()["texte"], "Il adore la synthwave")
        self.assertEqual(self.api.put("/api/souvenirs/nope", json={"texte": "x"}, headers=ORIGIN).status_code, 404)
        self.assertEqual(self.api.delete(f"/api/souvenirs/{sid}", headers=ORIGIN).status_code, 200)
        self.assertEqual(self.api.delete(f"/api/souvenirs/{sid}", headers=ORIGIN).status_code, 404)

    def test_tout_oublier_demande_confirmation(self):
        souvenirs.add("Un souvenir", "toi")
        self.assertEqual(self.api.delete("/api/souvenirs", headers=ORIGIN).status_code, 400)
        self.assertEqual(len(souvenirs.all_souvenirs()), 1)
        self.assertEqual(self.api.delete("/api/souvenirs?confirm=true", headers=ORIGIN).json(), {"oublies": 1})

    def test_reglages_et_conversation_privee(self):
        cfg = self.api.put("/api/config", json={"memory": {"ask_before": True, "max_injected": 10}}, headers=ORIGIN)
        self.assertEqual(cfg.json()["memory"], {"enabled": True, "ask_before": True, "max_injected": 10})
        self.assertEqual(self.api.put("/api/config", json={"memory": {"max_injected": 500}}, headers=ORIGIN).status_code,
                         422)
        cid = new_conversation()
        self.assertEqual(self.api.patch(f"/api/conversations/{cid}", json={"prive": True}, headers=ORIGIN).status_code,
                         200)
        self.assertTrue(conversations.get(cid)["prive"])


class Annuler(Base):
    """La puce « 🧠 Retenu » : Annuler défait l'outil et le note dans la conversation (le modèle le relit)."""

    def setUp(self):
        super().setUp()
        self.api = client()
        self.cid = new_conversation()

    def undo(self, call_id="c1"):
        return self.api.post("/api/souvenirs/annuler", json={"conversation_id": self.cid, "tool_call_id": call_id},
                             headers=ORIGIN)

    def test_annuler_un_ajout(self):
        asyncio.run(execute(self.cid, "retenir", {"texte": "Il aime le thé vert", "categorie": "preferences"}))
        self.assertEqual(self.undo().status_code, 200)
        self.assertEqual(souvenirs.all_souvenirs(), [])
        message = next(m for m in conversations.get(self.cid)["messages"] if m.get("souvenir"))
        self.assertTrue(message["souvenir"]["annule"])
        self.assertIn("Annulé ensuite", message["content"])
        self.assertEqual(self.undo().status_code, 409)  # pas deux fois
        self.assertEqual(self.undo("inconnu").status_code, 404)

    def test_annuler_une_mise_a_jour(self):
        souvenirs.add("Il a une Captur hybride", "toi")
        asyncio.run(execute(self.cid, "retenir", {"texte": "Il a une Captur hybride grise", "categorie": "toi"}))
        self.assertEqual(self.undo().status_code, 200)
        self.assertEqual([s["texte"] for s in souvenirs.all_souvenirs()], ["Il a une Captur hybride"])

    def test_annuler_un_oubli(self):
        saved, _ = souvenirs.add("Il joue à Stellar Blade", "preferences")
        asyncio.run(execute(self.cid, "oublier", {"id": saved["id"]}))
        self.assertEqual(souvenirs.all_souvenirs(), [])
        self.assertEqual(self.undo().status_code, 200)
        back = souvenirs.all_souvenirs()
        self.assertEqual([(s["id"], s["texte"], s["categorie"]) for s in back],
                         [(saved["id"], "Il joue à Stellar Blade", "preferences")])

    def test_privee_des_la_creation_et_contexte(self):
        souvenirs.add("Il adore Interstellar", "preferences")
        conv = self.api.post("/api/conversations", json={"prive": True}, headers=ORIGIN).json()
        self.assertTrue(conv["prive"])
        listed = next(c for c in self.api.get("/api/conversations").json() if c["id"] == conv["id"])
        self.assertTrue(listed["prive"])
        ctx = self.api.get(f"/api/agent/context?conversation_id={conv['id']}").json()
        self.assertEqual((ctx["souvenirs"]["prive"], ctx["souvenirs"]["injected"], ctx["layers"]["souvenirs"]), (True, 0, 0))
        ctx = self.api.get(f"/api/agent/context?conversation_id={self.cid}").json()
        self.assertEqual((ctx["souvenirs"]["injected"], ctx["souvenirs"]["total"]), (1, 1))
        self.assertGreater(ctx["layers"]["souvenirs"], 0)


if __name__ == "__main__":
    unittest.main()
