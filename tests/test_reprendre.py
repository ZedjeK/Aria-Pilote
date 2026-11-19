"""Reprendre d'ici : coupe, état remis à plat, résumé, copie gardée, fichiers remis (vraies sauvegardes), rétablir, API."""
import asyncio
import json
import unittest

from base import ORIGIN, client, new_conversation, workspace  # en premier : Aria tourne dans un dossier temporaire

from aria_pilote import agent, backups, conversations, rewind, tools


def write(cid, root, name, args):
    tool = tools.TOOLS[name]
    return asyncio.run(tool.run(tool.prepare(str(root), args), cid))


def user(text, **extra):
    return {"role": "user", "content": text, "created_at": "2026-11-19T10:00:00", **extra}


def assistant(text):
    return {"role": "assistant", "content": text}


def tool(task, name="read_file", **extra):
    return {"role": "tool", "name": name, "args": {}, "status": "ok", "content": "", "tool_call_id": "c", "task_id": task,
            **extra}


class Base(unittest.TestCase):
    def setUp(self):
        self.root = workspace("reprendre")
        (self.root / "app.py").write_text("x = 1\n", encoding="utf-8")
        self.cid = new_conversation(self.root)
        self.addCleanup(backups.set_running, self.cid, None)
        self.T1, self.T2, self.T9 = (backups.new_task_id() for _ in range(3))  # 12 caractères hexadécimaux, comme en vrai

    def messages(self, *messages, **state):
        def change(c):
            c["messages"] = list(messages)
            c.update(state)
        conversations.update(self.cid, change)

    def conv(self):
        return conversations.get(self.cid)

    def edit(self, task, old, new):
        """Une vraie modification de app.py, sous la tâche `task` ; renvoie la carte d'outil qui la porte."""
        backups.set_running(self.cid, task)
        write(self.cid, self.root, "edit_file", {"path": "app.py", "old_string": old, "new_string": new})
        backups.set_running(self.cid, None)
        item = [h for h in backups.history(self.cid) if h["task_id"] == task][-1]
        return tool(task, "edit_file", backup_id=item["backup_id"])


class Coupe(Base):
    def test_la_conversation_repart_du_message_et_son_texte_revient(self):
        self.messages(user("un"), assistant("a un"), user("deux", skill="revue-code"), assistant("a deux"), user("trois"))
        result = rewind.apply(self.cid, 2)
        self.assertEqual(result["text"], "/revue-code deux")  # avec sa compétence, tel que tapé
        conv = self.conv()
        self.assertEqual([m["content"] for m in conv["messages"]], ["un", "a un"])
        self.assertEqual(conv["rewound"], {"id": conv["rewound"]["id"], "count": 3, "from": 2, "files_changed": False})

    def test_etat_remis_a_plat(self):
        self.messages(user("un"), tool(self.T1), user("deux"), tool(self.T2),
                      todo=[{"content": "étape"}], plan={"status": "approved", "task_id": self.T2}, task_id=self.T2)
        rewind.apply(self.cid, 2)
        conv = self.conv()
        self.assertNotIn("todo", conv)
        self.assertNotIn("plan", conv)  # son plan était celui de la partie retirée
        self.assertEqual(conv["task_id"], self.T1)  # la dernière tâche encore dans la conversation

    def test_un_plan_d_avant_est_garde(self):
        self.messages(user("un"), tool(self.T1), user("deux"), tool(self.T2), plan={"status": "pending", "task_id": self.T1})
        rewind.apply(self.cid, 2)
        self.assertEqual(self.conv()["plan"], {"status": "pending", "task_id": self.T1})

    def test_refus(self):
        self.messages(user("un"), assistant("a un"))
        for index in (1, 2, -1, 9):
            with self.subTest(index=index), self.assertRaises(rewind.RewindError) as ctx:
                rewind.apply(self.cid, index)
            self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(len(self.conv()["messages"]), 2)  # rien n'a bougé

    def test_refus_pendant_que_l_agent_travaille(self):
        self.messages(user("un"), assistant("a un"))
        backups.set_running(self.cid, self.T9)
        with self.assertRaises(rewind.RewindError) as ctx:
            rewind.apply(self.cid, 0)
        self.assertIn("travaille encore", str(ctx.exception))
        self.assertEqual(len(self.conv()["messages"]), 2)

    def test_le_resume_suit_le_point_de_reprise(self):
        summary = {"text": "résumé", "upto": 4, "keep": None}
        self.messages(user("1"), assistant("a"), user("2"), assistant("b"), user("3"), assistant("c"), summary=summary)
        self.assertTrue(rewind.preview(self.conv(), 2)["summary_dropped"])
        rewind.apply(self.cid, 2)
        self.assertNotIn("summary", self.conv())  # il couvrait des messages qui reviennent dans le prompt
        self.messages(user("1"), assistant("a"), user("2"), assistant("b"), user("3"), assistant("c"), summary=summary)
        self.assertFalse(rewind.preview(self.conv(), 4)["summary_dropped"])
        rewind.apply(self.cid, 4)
        self.assertEqual(self.conv()["summary"], summary)  # le point de reprise est après : le résumé reste vrai

    def test_copie_gardee_dans_la_limite(self):
        for i in range(7):
            self.messages(user("un"), assistant("a"), user(f"suite {i}"), assistant("b"))
            rewind.apply(self.cid, 2)
        kept = json.loads(rewind._kept_path(self.cid).read_text(encoding="utf-8"))
        self.assertEqual(len(kept), rewind.MAX_KEPT)
        self.assertEqual(kept[-1]["messages"][0]["content"], "suite 6")

    def test_pieces_jointes_d_origine(self):
        message = user("Lis @notes.md et ça", files=[{"name": "notes.md", "text": "x"}, {"name": "rapport.pdf", "text": "y"}],
                       images=[{"file": "a.png"}])
        self.messages(message, assistant("a"))
        lost = rewind.preview(self.conv(), 0)["lost_attachments"]
        self.assertEqual(lost, ["rapport.pdf", "image 1"])  # le @fichier se relit à l'envoi, pas le reste


class Fichiers(Base):
    def test_apercu_des_taches_et_des_commandes(self):
        card = self.edit(self.T2, "x = 1", "x = 2")
        run = tool(self.T2, "run_command", args={"command": "pip install requests"})
        self.messages(user("un"), tool(self.T1), user("deux"), card, run)
        info = rewind.preview(self.conv(), 2)
        self.assertEqual(info["removed"], 3)
        self.assertEqual([t["task_id"] for t in info["tasks"]], [self.T2])
        self.assertEqual([f["path"] for f in info["tasks"][0]["files"]], ["app.py"])
        self.assertEqual(info["tasks"][0]["commands"], ["pip install requests"])
        self.assertTrue(info["can_restore_files"])
        self.assertEqual(info["straddling"], [])

    def test_fichiers_remis_a_l_identique(self):
        card = self.edit(self.T2, "x = 1", "x = 2")
        self.messages(user("un"), user("deux"), card)
        result = rewind.apply(self.cid, 1, restore_files=True)
        self.assertEqual((self.root / "app.py").read_text(encoding="utf-8"), "x = 1\n")
        self.assertEqual(result["restored"][self.T2]["restored"], ["app.py"])
        conv = self.conv()
        self.assertTrue(conv["rewound"]["files_changed"])
        self.assertNotIn(self.T2, conv.get("undone_tasks") or {})  # cette tâche n'est plus dans la conversation

    def test_sans_la_case_les_fichiers_ne_bougent_pas(self):
        card = self.edit(self.T2, "x = 1", "x = 2")
        self.messages(user("un"), user("deux"), card)
        rewind.apply(self.cid, 1)
        self.assertEqual((self.root / "app.py").read_text(encoding="utf-8"), "x = 2\n")
        self.assertFalse(self.conv()["rewound"]["files_changed"])

    def test_une_tache_commencee_avant_n_est_jamais_annulee_a_moitie(self):
        first = self.edit(self.T1, "x = 1", "x = 2")  # avant le point de reprise
        second = self.edit(self.T1, "x = 2", "x = 3")  # après, même tâche (« Continuer »)
        self.messages(user("un"), first, user("deux"), second)
        info = rewind.preview(self.conv(), 2)
        self.assertEqual(info["straddling"], [self.T1])
        self.assertFalse(info["can_restore_files"])
        with self.assertRaises(rewind.RewindError) as ctx:
            rewind.apply(self.cid, 2, restore_files=True)
        self.assertIn("a commencé avant ce message", str(ctx.exception))
        self.assertEqual((self.root / "app.py").read_text(encoding="utf-8"), "x = 3\n")  # rien n'a été touché
        self.assertEqual(len(self.conv()["messages"]), 4)

    def test_aucun_fichier_a_remettre(self):
        self.messages(user("un"), tool(self.T1))
        with self.assertRaises(rewind.RewindError):
            rewind.apply(self.cid, 0, restore_files=True)

    def test_conflit_laisse_tel_quel(self):
        card = self.edit(self.T2, "x = 1", "x = 2")
        self.messages(user("un"), user("deux"), card)
        (self.root / "app.py").write_text("retouché par moi\n", encoding="utf-8")
        result = rewind.apply(self.cid, 1, restore_files=True)
        self.assertEqual(result["restored"][self.T2]["skipped"], ["app.py"])
        self.assertEqual((self.root / "app.py").read_text(encoding="utf-8"), "retouché par moi\n")
        self.assertFalse(self.conv()["rewound"]["files_changed"])  # rien n'a été remis : rétablir reste permis


class Retablir(Base):
    def test_retablir_remet_tout_en_place(self):
        summary = {"text": "résumé", "upto": 2, "keep": None}
        self.messages(user("un"), tool(self.T1), user("deux"), tool(self.T2), assistant("fin"), summary=summary,
                      todo=[{"content": "étape"}], plan={"status": "approved", "task_id": self.T2}, task_id=self.T2)
        before = self.conv()["messages"]
        rewind.apply(self.cid, 2)
        rewound = self.conv()["rewound"]["id"]
        rewind.restore(self.cid, rewound)
        conv = self.conv()
        self.assertEqual(conv["messages"], before)
        self.assertEqual((conv["task_id"], conv["plan"]["task_id"], conv["todo"]), (self.T2, self.T2, [{"content": "étape"}]))
        self.assertNotIn("rewound", conv)
        self.assertEqual(json.loads(rewind._kept_path(self.cid).read_text(encoding="utf-8")), [])

    def test_refus_apres_une_suite(self):
        self.messages(user("un"), assistant("a"), user("deux"), assistant("b"))
        rewind.apply(self.cid, 2)
        rewound = self.conv()["rewound"]["id"]
        conversations.update(self.cid, lambda c: conversations.add_user_message(c, "nouveau départ"))
        self.assertNotIn("rewound", self.conv())  # le bandeau disparaît dès que la conversation reprend
        with self.assertRaises(rewind.RewindError) as ctx:
            rewind.restore(self.cid, rewound)
        self.assertIn("a continué depuis", str(ctx.exception))

    def test_refus_si_des_fichiers_ont_ete_remis(self):
        card = self.edit(self.T2, "x = 1", "x = 2")
        self.messages(user("un"), user("deux"), card)
        rewind.apply(self.cid, 1, restore_files=True)
        with self.assertRaises(rewind.RewindError) as ctx:
            rewind.restore(self.cid, self.conv()["rewound"]["id"])
        self.assertIn("ne les rejouerait pas", str(ctx.exception))

    def test_reprise_inconnue(self):
        self.messages(user("un"))
        with self.assertRaises(rewind.RewindError) as ctx:
            rewind.restore(self.cid, "inconnue")
        self.assertEqual(ctx.exception.status, 404)


class Api(Base):
    def setUp(self):
        super().setUp()
        self.http = client()

    def test_apercu_reprise_et_retablissement(self):
        self.messages(user("un"), assistant("a un"), user("deux"), assistant("a deux"))
        r = self.http.get(f"/api/conversations/{self.cid}/rewind", params={"index": 2})
        self.assertEqual((r.status_code, r.json()["removed"]), (200, 2))
        self.assertEqual(len(self.conv()["messages"]), 4)  # l'aperçu ne touche à rien
        r = self.http.post(f"/api/conversations/{self.cid}/rewind", json={"index": 2}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["text"], "deux")
        self.assertEqual(len(r.json()["conversation"]["messages"]), 2)
        rewound = r.json()["conversation"]["rewound"]["id"]
        r = self.http.post(f"/api/conversations/{self.cid}/rewind/restore", json={"id": rewound}, headers=ORIGIN)
        self.assertEqual((r.status_code, len(r.json()["conversation"]["messages"])), (200, 4))

    def test_refus(self):
        self.messages(user("un"), assistant("a un"))
        self.assertEqual(self.http.get("/api/conversations/inconnue/rewind", params={"index": 0}).status_code, 404)
        self.assertEqual(self.http.get(f"/api/conversations/{self.cid}/rewind", params={"index": 1}).status_code, 400)
        r = self.http.post(f"/api/conversations/{self.cid}/rewind", json={"index": 1}, headers=ORIGIN)
        self.assertEqual(r.status_code, 400)
        backups.set_running(self.cid, self.T9)
        r = self.http.post(f"/api/conversations/{self.cid}/rewind", json={"index": 0}, headers=ORIGIN)
        self.assertEqual(r.status_code, 409)
        # sans l'origine de l'interface, la requête est refusée comme toute écriture
        self.assertEqual(self.http.post(f"/api/conversations/{self.cid}/rewind", json={"index": 0},
                                        headers={"Origin": "http://autre.example"}).status_code, 403)


if __name__ == "__main__":
    unittest.main()
