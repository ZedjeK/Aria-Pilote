"""Bac à sable, retour des fichiers : sandbox_diff et sandbox_apply. Ce qui revient, ce qui est refusé (noms dangereux,
conflits, binaires, caches, dossiers protégés), l'accord toujours demandé, le circuit habituel des écritures (sauvegarde,
« Annuler »), les fins de ligne. Faux wsl.exe pour l'essentiel ; le vrai essai ne tourne que sur demande
(ARIA_TEST_WSL=1) : il crée puis supprime une vraie distribution.
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import unittest

from base import workspace
from test_bac_a_sable_outil import Base, project

from aria_pilote import agent, backups, bac_a_sable as bas, bac_retour, conversations, toolrun, tools


class Retour(Base):
    def setUp(self):
        super().setUp()
        self.root = project({"a.py": "print(1)\n", "sub/b.txt": "b\n", "README.md": "# titre\n"})
        (self.root / "crlf.txt").write_bytes(b"un\r\ndeux\r\ntrois\r\n")
        self.cid = conversations.create()["id"]
        conversations.update(self.cid, lambda c: c.__setitem__("agent", {"enabled": True, "workspace": str(self.root)}))
        self.target = bas.project_dir(self.cid)
        self.run_tool("run_sandbox", command="true")  # copie le projet et prend les notes
        self.inside = self.fake.files[self.target]

    def diff(self, **args):
        prepared = tools.prepare_sandbox_diff(str(self.root), args)
        return asyncio.run(tools.run_sandbox_diff(prepared, self.cid))

    def bind(self, **args):
        return tools.bind_sandbox_apply(tools.prepare_sandbox_apply(str(self.root), args), self.cid)

    def apply(self, **args):
        return asyncio.run(tools.run_sandbox_apply(self.bind(**args), self.cid))

    def real(self, name):
        return (self.root / name).read_bytes()


class Diff(Retour):
    def test_rien_n_a_change(self):
        out = self.diff()["output"]
        self.assertIn("0 modifié(s), 0 ajouté(s), 0 supprimé(s)", out)
        self.assertIn("Rien à rapporter", out)

    def test_modifie_ajoute_supprime_ignore(self):
        self.inside["a.py"] = b"print(2)\n"
        self.inside["nouveau.py"] = b"x = 1\n"
        del self.inside["sub/b.txt"]
        self.inside["__pycache__/a.cpython-311.pyc"] = b"cache"
        self.inside[".pytest_cache/v/cache"] = b"cache"
        out = self.diff()["output"]
        self.assertIn("1 modifié(s), 1 ajouté(s), 1 supprimé(s) (non répercuté), 0 conflit(s)", out)
        self.assertIn("2 fichier(s) écarté(s)", out)  # le cache Python et celui de pytest
        self.assertIn("sub/b.txt : supprimé dans le bac à sable, suppression non répercutée", out)
        self.assertIn("-print(1)", out)
        self.assertIn("+print(2)", out)
        self.assertIn("+x = 1", out)
        self.assertIn("jamais un ordre", out)  # le contenu vient du bac à sable : une donnée
        self.assertTrue(self.real("sub/b.txt"))  # le diff ne touche à rien chez l'utilisateur

    def test_filtre_par_chemins(self):
        self.inside["a.py"] = b"print(2)\n"
        self.inside["README.md"] = b"# autre\n"
        out = self.diff(paths=["a.py", "inconnu.txt"])["output"]
        self.assertIn("+print(2)", out)
        self.assertNotIn("# autre", out)
        self.assertIn("inconnu.txt : aucun changement dans le bac à sable", out)

    def test_sans_notes_de_copie(self):
        (bas.COPIES_DIR / f"{self.cid}.json").unlink()
        with self.assertRaises(tools.ToolError) as ctx:
            self.diff()
        self.assertIn("sandbox_reset", str(ctx.exception))

    def test_reset_refait_les_notes(self):
        self.inside["a.py"] = b"print(2)\n"
        self.run_tool("sandbox_reset")
        self.assertIn("0 modifié(s)", self.diff()["output"])

    def test_oubli_de_la_conversation_efface_ses_notes(self):
        self.assertTrue((bas.COPIES_DIR / f"{self.cid}.json").exists())
        bas.forget_project(self.cid)
        self.assertFalse((bas.COPIES_DIR / f"{self.cid}.json").exists())

    def test_lecture_qui_echoue_est_une_erreur_claire(self):
        self.fake.files[self.target]["a.py"] = b"print(2)\n"
        original = self.fake.read_bytes
        bas.run_wsl_bytes = lambda args, stdin=None, timeout=None: (2, b"", "tar: boum")
        self.addCleanup(setattr, bas, "run_wsl_bytes", original)
        with self.assertRaises(tools.ToolError) as ctx:
            self.diff()
        self.assertIn("boum", str(ctx.exception))


class Apply(Retour):
    def test_ecrit_par_le_circuit_habituel(self):
        self.inside["a.py"] = b"print(2)\n"
        self.inside["sub/nouveau.py"] = b"x = 1\n"
        result = self.apply()
        self.assertEqual(self.real("a.py"), b"print(2)\n")
        self.assertEqual(self.real("sub/nouveau.py"), b"x = 1\n")
        self.assertIn("2 fichiers enregistrés", result["output"])
        ids = {b["path"]: b["backup_id"] for b in result["backups"]}
        self.assertEqual(set(ids), {"a.py", "sub/nouveau.py"})
        # « Annuler cette modif » : la sauvegarde remet l'ancien contenu
        backups.restore(self.cid, ids["a.py"])
        self.assertEqual(self.real("a.py"), b"print(1)\n")
        # Ce qui est revenu n'est plus « changé » : la comparaison part de son nouvel état
        self.assertIn("0 modifié(s), 0 ajouté(s)", self.diff()["output"])

    def test_fichiers_choisis_seulement(self):
        self.inside["a.py"] = b"print(2)\n"
        self.inside["README.md"] = b"# autre\n"
        result = self.apply(paths=["a.py", "inconnu.txt"])
        self.assertEqual(self.real("a.py"), b"print(2)\n")
        self.assertEqual(self.real("README.md"), b"# titre\n")
        self.assertIn("inconnu.txt : aucun changement", result["output"])
        self.assertIn("# autre", self.diff()["output"])  # l'autre reste à rapporter

    def test_rien_a_rapporter_n_ouvre_pas_d_accord(self):
        with self.assertRaises(tools.ToolError) as ctx:
            self.bind()
        self.assertIn("Rien à rapporter", str(ctx.exception))

    def test_fins_de_ligne_de_l_utilisateur_conservees(self):
        self.inside["crlf.txt"] = "un\ndeux modifié\ntrois\n".encode("utf-8")  # édité en LF dans le bac
        self.inside["sub/b.txt"] = b"b\nc\n"  # un fichier LF le reste
        self.apply()
        self.assertEqual(self.real("crlf.txt"), "un\r\ndeux modifié\r\ntrois\r\n".encode("utf-8"))
        self.assertEqual(self.real("sub/b.txt"), b"b\nc\n")

    def test_seules_les_fins_de_ligne_different(self):
        self.inside["crlf.txt"] = b"un\ndeux\ntrois\n"  # dos2unix dans le bac : rien de réel n'a changé
        with self.assertRaises(tools.ToolError) as ctx:
            self.bind()
        self.assertIn("identique à ton fichier", str(ctx.exception))
        self.assertEqual(self.real("crlf.txt"), b"un\r\ndeux\r\ntrois\r\n")
        self.assertIn("0 modifié(s)", self.diff()["output"])  # et ça ne revient plus à chaque comparaison

    def test_conflits_jamais_ecrases(self):
        (self.root / "a.py").write_bytes(b"print('moi')\n")  # l'utilisateur a modifié de son côté
        (self.root / "sub" / "b.txt").unlink()  # ou supprimé
        (self.root / "pris.txt").write_bytes(b"a moi\n")  # ou créé le même nom
        self.inside["a.py"] = b"print(2)\n"
        self.inside["sub/b.txt"] = b"b modifie\n"
        self.inside["pris.txt"] = b"a l'agent\n"
        with self.assertRaises(tools.ToolError) as ctx:
            self.bind()
        message = str(ctx.exception)
        self.assertIn("a.py : conflit, modifié de ton côté", message)
        self.assertIn("sub/b.txt : conflit, supprimé de ton côté", message)
        self.assertIn("pris.txt : conflit, existe déjà dans ton projet", message)
        self.assertEqual(self.real("a.py"), b"print('moi')\n")
        self.assertEqual(self.real("pris.txt"), b"a moi\n")
        self.assertIn("3 conflit(s)", self.diff()["output"])
        # Un conflit n'empêche pas le reste de revenir
        self.inside["README.md"] = b"# autre\n"
        result = self.apply()
        self.assertEqual(self.real("README.md"), b"# autre\n")
        self.assertIn("a.py : conflit", result["output"])

    def test_noms_dangereux_refuses(self):
        for name in ("a.txt:cache", "CON", "dir/nul.txt", ".aria/hooks.json", ".git/hooks/pre-commit", "fin.",
                     "etoile*.txt"):
            self.inside[name] = b"x"
        self.inside["ok.txt"] = b"bien\n"
        result = self.apply()
        self.assertEqual(self.real("ok.txt"), b"bien\n")
        self.assertIn("flux de données caché", result["output"])
        self.assertIn("nom réservé par Windows", result["output"])
        self.assertIn("dossier protégé", result["output"])
        for forbidden in ("a.txt", "CON", "nul.txt", ".aria", ".git", "fin", "etoile"):
            self.assertFalse(any(p.name.startswith(forbidden) for p in self.root.rglob("*")), forbidden)

    def test_binaires_et_non_utf8_refuses(self):
        self.inside["donnees.bin"] = b"\x00\x01\x02binaire"
        self.inside["latin.txt"] = "é".encode("latin-1")
        self.inside["bon.txt"] = "été\n".encode("utf-8")
        result = self.apply()
        self.assertEqual(self.real("bon.txt"), "été\n".encode("utf-8"))
        self.assertIn("donnees.bin : refusé, binaire", result["output"])
        self.assertIn("latin.txt : refusé, pas de l'UTF-8", result["output"])
        self.assertFalse((self.root / "donnees.bin").exists() or (self.root / "latin.txt").exists())

    def test_trop_gros_refuse(self):
        self.inside["gros.txt"] = b"x" * (bac_retour.MAX_FILE_BYTES + 1)
        self.inside["petit.txt"] = b"p\n"
        result = self.apply()
        self.assertIn("gros.txt : refusé, trop gros", result["output"])
        self.assertFalse((self.root / "gros.txt").exists())

    def test_trente_fichiers_au_plus(self):
        for i in range(35):
            self.inside[f"gen/f{i:02d}.txt"] = f"{i}\n".encode()
        prepared = self.bind()
        self.assertEqual(len(prepared["files"]), bac_retour.MAX_FILES)
        self.assertIn("5 autre(s) fichier(s) non traité(s)", "\n".join(prepared["notes"]))
        self.apply()
        self.assertIn("5 ajouté(s)", self.diff()["output"])  # les suivants sont encore à rapporter

    def test_le_fichier_change_pendant_la_lecture_du_diff(self):
        self.inside["a.py"] = b"print(2)\n"
        prepared = self.bind()
        (self.root / "a.py").write_bytes(b"print('pendant')\n")
        with self.assertRaises(tools.ToolError) as ctx:
            asyncio.run(tools.run_sandbox_apply(prepared, self.cid))
        self.assertIn("a changé depuis l'aperçu", str(ctx.exception))
        self.assertEqual(self.real("a.py"), b"print('pendant')\n")

    def test_projet_git_ignore_ce_que_git_ignore(self):
        if shutil.which("git") is None:
            self.skipTest("git absent")
        (self.root / ".gitignore").write_text("*.log\nbuild/\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        self.inside["debug.log"] = b"journal\n"
        self.inside["build/sortie.txt"] = b"compile\n"
        self.inside["nouveau.py"] = b"x = 1\n"
        out = self.diff()["output"]
        self.assertIn("1 ajouté(s)", out)
        self.assertIn("2 fichier(s) écarté(s)", out)
        self.assertNotIn("journal", out)


class Accord(Retour):
    """L'accord est toujours demandé, même si les modifs sont autorisées pour la conversation."""

    def run_through_agent(self, decision):
        out, asked = {}, []

        async def scenario():
            async for ev in toolrun.run_tool(self.cid, str(self.root), "sandbox_apply", {}, "appel", out):
                payload = json.loads(ev.removeprefix("data: "))
                if payload["type"] == "approval":
                    asked.append(payload)
                    toolrun.resolve_approval(payload["approval_id"], decision)

        asyncio.run(scenario())
        return asked, out["result"]

    def test_toujours_demande_meme_avec_les_modifs_autorisees(self):
        conversations.update(self.cid, lambda c: c["agent"].update({"auto_write": True}))
        self.inside["a.py"] = b"print(2)\n"
        asked, result = self.run_through_agent("allow")
        self.assertEqual(len(asked), 1)
        self.assertEqual(asked[0]["kind"], "sandbox_apply")
        self.assertIn("+print(2)", asked[0]["preview"])  # le diff réel est dans la carte
        self.assertEqual(result["status"], "ok")
        self.assertEqual(self.real("a.py"), b"print(2)\n")

    def test_refus(self):
        self.inside["a.py"] = b"print(2)\n"
        asked, result = self.run_through_agent("deny")
        self.assertEqual(result["status"], "denied")
        self.assertEqual(self.real("a.py"), b"print(1)\n")
        self.assertIn("1 modifié(s)", self.diff()["output"])  # toujours à rapporter

    def test_pas_de_regle_ni_d_autorisation_de_session(self):
        self.assertIsNone(toolrun.APPROVAL_FLAGS["sandbox_apply"])
        self.inside["a.py"] = b"print(2)\n"
        out, refused = {}, []

        async def scenario():
            async for ev in toolrun.run_tool(self.cid, str(self.root), "sandbox_apply", {}, "appel", out):
                payload = json.loads(ev.removeprefix("data: "))
                if payload["type"] == "approval":
                    try:  # « Autoriser pour cette conversation » n'existe pas pour cette action
                        toolrun.resolve_approval(payload["approval_id"], "allow_session")
                    except toolrun.ApprovalError as exc:
                        refused.append(str(exc))
                    toolrun.resolve_approval(payload["approval_id"], "allow")

        asyncio.run(scenario())
        self.assertEqual(refused, ["Cette action se valide une à une."])
        self.assertNotIn("auto_write", conversations.get(self.cid)["agent"])
        self.assertEqual(out["result"]["status"], "ok")

    def test_rien_a_rapporter_pas_de_demande(self):
        asked, result = self.run_through_agent("allow")
        self.assertEqual(asked, [])
        self.assertEqual(result["status"], "error")
        self.assertIn("Rien à rapporter", result["output"])

    def test_verrou_du_plan_comptage_des_ecritures_et_disponibilite(self):
        self.assertIn("sandbox_apply", agent.LOCKED_KINDS)
        self.assertEqual(agent.tool_kind("sandbox_apply"), "sandbox_apply")
        self.assertTrue(agent._wrote("sandbox_apply", {"status": "ok"}))  # les vérifications de fin de tâche se lancent
        self.assertFalse(agent._wrote("sandbox_diff", {"status": "ok"}))
        conv = {"id": self.cid, "messages": [], "sandbox": True}
        names = agent.task_tools(conv, str(self.root))[0]
        self.assertIn("sandbox_apply", names)
        self.assertIn("sandbox_diff", names)
        names = agent.task_tools({**conv, "sandbox": False}, str(self.root))[0]
        self.assertNotIn("sandbox_apply", names)
        self.assertNotIn("sandbox_diff", names)


class Arguments(unittest.TestCase):
    def test_paths(self):
        self.assertIsNone(tools._paths_arg({}))
        self.assertIsNone(tools._paths_arg({"paths": []}))
        self.assertEqual(tools._paths_arg({"paths": ["a.py", "./sub\\b.txt"]}), ["a.py", "sub/b.txt"])
        self.assertEqual(tools._paths_arg({"paths": "a.py"}), ["a.py"])
        self.assertEqual(tools._paths_arg({"paths": '["a.py", "b.py"]'}), ["a.py", "b.py"])  # JSON envoyé en texte
        for bad in ({"paths": [1]}, {"paths": [""]}, {"paths": {"a": 1}}, {"paths": ["x"] * 101}):
            with self.assertRaises(tools.ToolError, msg=bad):
                tools._paths_arg(bad)

    def test_noms(self):
        for good in ("a.py", "dir/b.txt", "é.txt", "dir avec espace/c d.txt", "-tiret.txt", ".gitignore"):
            self.assertIsNone(bac_retour.name_refusal(good), good)
        for bad in ("a.txt:flux", "CON", "con.txt", "COM1", "dir/LPT9.log", "x/../y", "/abs", "a//b", ".git/config",
                    "sub/.git/x", ".aria/hooks.json", "fin.", "fin ", "a*b", "a?b", 'a"b', "a|b", "a<b", "a\\b",
                    "ctrl\x01.txt", "x" * 241):
            self.assertIsNotNone(bac_retour.name_refusal(bad), repr(bad))

    def test_fins_de_ligne(self):
        keep = tools._keep_line_endings
        self.assertEqual(keep("a\r\nb\r\n", "a\nb\nc\n"), "a\r\nb\r\nc\r\n")
        self.assertEqual(keep("a\r\nb\r\n", "a\r\nb\r\nc\r\n"), "a\r\nb\r\nc\r\n")  # déjà en CRLF : pas de double CR
        self.assertEqual(keep("a\nb\n", "a\nb\nc\n"), "a\nb\nc\n")
        self.assertEqual(keep("a\r\nb\n", "a\nb\nc\n"), "a\nb\nc\n")  # mélange : on ne devine pas
        self.assertEqual(keep(None, "nouveau\n"), "nouveau\n")
        self.assertEqual(keep("", "x\n"), "x\n")


@unittest.skipUnless(sys.platform == "win32" and os.environ.get("ARIA_TEST_WSL") == "1",
                     "vrai WSL : ARIA_TEST_WSL=1 (crée puis supprime une distribution d'essai)")
class VraiWsl(unittest.TestCase):
    def setUp(self):
        self.original = bas.DISTRO
        bas.DISTRO = "aria-bac-a-sable-test"
        self.addCleanup(self.restore)

    def restore(self):
        bas.delete()
        bas.DISTRO = self.original
        bas.forget_cache()

    def test_diff_et_retour_de_bout_en_bout(self):
        root = workspace("retour")
        files = {"a.py": b"print(1)\n", "crlf.txt": b"un\r\ndeux\r\ntrois\r\n", "sub/b.txt": b"b\n",
                 "dossier espace/c d.txt": "é\n".encode("utf-8"), "-tiret.txt": b"t\n"}
        for name, data in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_bytes(data)
        cid = "0123456789ab"
        bas.create("alpine")
        bas.copy_project(cid, root)
        # L'agent travaille : modifie, ajoute, supprime, produit des caches, un binaire et des noms piégés
        result = bas.run_in_project(cid, "echo 'print(2)' > a.py; printf 'un\\ndeux modifie\\ntrois\\n' > crlf.txt; "
                                         "mkdir -p neuf; echo n > neuf/x.txt; rm sub/b.txt; printf '\\000\\001' > bin.dat; "
                                         "echo p > 'f:flux'; echo p > CON; mkdir -p .aria __pycache__; echo p > .aria/h; "
                                         "echo c > __pycache__/z.pyc; echo t2 > ./-tiret.txt")
        self.assertEqual(result["code"], 0, result)
        found = tools._sandbox_changes(cid, str(root), None)
        self.assertEqual(sorted(f["rel"] for f in found["files"]), ["-tiret.txt", "a.py", "crlf.txt", "neuf/x.txt"])
        self.assertEqual(set(found["scan"]["refuses"]), {"f:flux", "CON", ".aria/h", "bin.dat"} - {"bin.dat"})
        self.assertIn("bin.dat", "\n".join(found["notes"]))
        self.assertEqual(found["scan"]["supprimes"], ["sub/b.txt"])
        self.assertEqual(found["scan"]["ignores"], 0)  # le cache Python est élagué : même pas listé par le bac
        # Retour : le circuit habituel, sans accord ici (les accords sont testés plus haut)
        prepared = tools.bind_sandbox_apply(tools.prepare_sandbox_apply(str(root), {}), cid)
        out = asyncio.run(tools.run_sandbox_apply(prepared, cid))
        self.assertIn("4 fichiers enregistrés", out["output"])
        self.assertEqual((root / "a.py").read_bytes(), b"print(2)\n")
        self.assertEqual((root / "crlf.txt").read_bytes(), b"un\r\ndeux modifie\r\ntrois\r\n")  # CRLF conservé
        self.assertEqual((root / "neuf/x.txt").read_bytes(), b"n\n")
        self.assertEqual((root / "-tiret.txt").read_bytes(), b"t2\n")
        self.assertTrue((root / "sub/b.txt").exists())  # la suppression n'est pas répercutée
        for name in ("f:flux", "CON", ".aria", "bin.dat"):
            self.assertFalse((root / name).exists(), name)
        # Après le retour : plus rien à rapporter (dont les fichiers refusés, qui restent signalés)
        again = tools._sandbox_changes(cid, str(root), None)
        self.assertEqual(again["files"], [])
        # Un conflit : l'utilisateur modifie a.py, l'agent aussi
        (root / "a.py").write_bytes(b"print('moi')\n")
        bas.run_in_project(cid, "echo 'print(3)' > a.py")
        again = tools._sandbox_changes(cid, str(root), None)
        self.assertEqual(again["scan"]["conflits"], {"a.py": "modifié de ton côté depuis la copie"})
        # reset : la copie repart du projet réel, les notes aussi
        bas.reset_project(cid, root)
        self.assertEqual(tools._sandbox_changes(cid, str(root), None)["scan"]["conflits"], {})
        self.assertEqual(bas.run_in_project(cid, "cat a.py")["sortie"], "print('moi')")


if __name__ == "__main__":
    unittest.main()
