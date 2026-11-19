"""@fichier : fichiers proposés après un « @ », lecture des @chemin d'un message, refus (secrets, hors du dossier), API."""
import base64
import os
import shutil
import subprocess
import unittest
from unittest import mock

from base import ORIGIN, client, new_conversation, workspace  # en premier : Aria tourne dans un dossier temporaire

from aria_pilote import agent, attachments, conversations, mentions
from aria_pilote.server import manager


def make(root, files):
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))


class Base(unittest.TestCase):
    def setUp(self):
        mentions._cache.clear()
        self.root = workspace("mentions")
        make(self.root, {
            "README.md": "# Projet", "src/app.py": "print('app')", "src/utils/outils.py": "x = 1",
            "docs/notes de reunion.md": "ordre du jour", "app.log": "ligne",
            ".env": "TOKEN=abc", "prod.env": "TOKEN=abc", ".env.local": "TOKEN=abc", "cle.pem": "-----BEGIN-----", "logo.png": b"\x89PNG\r\n\x1a\n\x00\x00",
            "node_modules/pkg/index.js": "module.exports = 1", ".venv/lib/x.py": "x",
        })


class Jetons(unittest.TestCase):
    def test_chemins_cites(self):
        self.assertEqual(mentions.tokens("Regarde @src/app.py, puis @README.md."), ["src/app.py", "README.md"])
        self.assertEqual(mentions.tokens('Et @"docs/notes de reunion.md" aussi'), ["docs/notes de reunion.md"])
        self.assertEqual(mentions.tokens("@a.py @a.py @b.py"), ["a.py", "b.py"])  # sans doublon, dans l'ordre
        self.assertEqual(mentions.tokens("Lis @src\\app.py"), ["src/app.py"])  # séparateur Windows

    def test_une_adresse_n_est_pas_une_mention(self):
        self.assertEqual(mentions.tokens("écris à jean@exemple.fr ou à jean.dupont@cti.fr"), [])
        self.assertEqual(mentions.tokens("le paquet @types/node"), ["types/node"])  # écarté plus tard : pas un fichier
        self.assertEqual(mentions.tokens("rien ici, ni @ seul"), [])


class Propositions(Base):
    def test_classement(self):
        names = [c["path"] for c in mentions.candidates(self.root, "app")]
        self.assertEqual(names[:2], ["app.log", "src/app.py"])  # même rang : le moins profond d'abord
        self.assertEqual(mentions.candidates(self.root, "app.py")[0]["path"], "src/app.py")
        self.assertEqual([c["path"] for c in mentions.candidates(self.root, "pp.p")], ["src/app.py"])  # nom qui la contient
        self.assertEqual(mentions.candidates(self.root, "OUTILS")[0]["path"], "src/utils/outils.py")  # sans casse
        self.assertEqual(mentions.candidates(self.root, "utils/out")[0]["path"], "src/utils/outils.py")  # par le chemin

    def test_sans_saisie_les_moins_profonds_d_abord(self):
        names = [c["path"] for c in mentions.candidates(self.root, "")]
        self.assertEqual(names[:2], ["app.log", "README.md"])  # le moins profond, puis le plus court
        self.assertLessEqual(len(names), mentions.SUGGESTIONS)

    def test_jamais_un_secret_ni_un_format_inutile_ni_un_dossier_ignore(self):
        names = [c["path"] for c in mentions.candidates(self.root, "", limit=50)]
        for hidden in (".env", "prod.env", ".env.local", "cle.pem", "logo.png", "node_modules/pkg/index.js", ".venv/lib/x.py"):
            self.assertNotIn(hidden, names)
        self.assertIn("docs/notes de reunion.md", names)

    def test_taille_et_limite(self):
        found = mentions.candidates(self.root, "readme")
        self.assertEqual(found, [{"path": "README.md", "size": len("# Projet")}])
        self.assertEqual(len(mentions.candidates(self.root, "", limit=2)), 2)

    @unittest.skipIf(shutil.which("git") is None, "git absent")
    def test_un_depot_git_ne_montre_pas_ce_qu_il_ignore(self):
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        make(self.root, {".gitignore": "secret-local.txt\n", "secret-local.txt": "x", "visible.txt": "y"})
        mentions._cache.clear()
        names = [c["path"] for c in mentions.candidates(self.root, "", limit=50)]
        self.assertIn("visible.txt", names)
        self.assertNotIn("secret-local.txt", names)

    def test_dossier_invalide(self):
        with self.assertRaises(mentions.workspace.WorkspaceError):
            mentions.candidates(self.root / "absent", "x")


class Lecture(Base):
    def read(self, text, already=0):
        return mentions.read(self.root, text, already)

    def test_fichier_lu_avec_son_chemin(self):
        files = self.read("Explique @src/app.py")
        self.assertEqual([f["name"] for f in files], ["src/app.py"])
        self.assertEqual(files[0]["text"], "print('app')")
        self.assertEqual(files[0]["kind"], "texte")

    def test_nom_avec_espace_entre_guillemets(self):
        self.assertEqual(self.read('Résume @"docs/notes de reunion.md"')[0]["text"], "ordre du jour")

    def test_ce_qui_n_est_pas_un_fichier_reste_du_texte(self):
        self.assertEqual(self.read("Merci @dependabot, et @types/node, et @src"), [])  # un pseudo, un paquet, un dossier
        self.assertEqual(self.read("@absent.py"), [])
        self.assertEqual(self.read("jean@exemple.fr"), [])
        self.assertEqual(mentions.read(None, "@src/app.py"), [])  # pas de dossier attaché : rien n'est joint

    def test_sans_doublon_et_dans_la_limite(self):
        self.assertEqual(len(self.read("@README.md @README.md")), 1)
        with self.assertRaises(mentions.MentionError) as ctx:
            self.read("@README.md @src/app.py", already=attachments.MAX_PER_MESSAGE - 1)
        self.assertIn("5 fichiers au plus", str(ctx.exception))

    def test_secrets_refuses(self):
        for rel in (".env", "prod.env", ".env.local", "cle.pem"):
            with self.subTest(rel=rel), self.assertRaises(mentions.MentionError) as ctx:
                self.read(f"Lis @{rel}")
            self.assertIn("ressemble à un secret", str(ctx.exception))
            self.assertIn("📎", str(ctx.exception))

    def test_hors_du_dossier_refuse(self):
        outside = workspace("dehors")
        make(outside, {"hors.txt": "secret"})
        with self.assertRaises(mentions.MentionError) as ctx:
            self.read("@../" + outside.name + "/hors.txt")
        self.assertIn("en dehors du dossier de travail", str(ctx.exception))

    def test_lien_vers_l_exterieur_refuse(self):
        outside = workspace("dehors")
        make(outside, {"hors.txt": "secret"})
        try:
            os.symlink(outside / "hors.txt", self.root / "lien.txt")
        except (OSError, NotImplementedError):
            self.skipTest("création de lien impossible ici (droits)")
        with self.assertRaises(mentions.MentionError):
            self.read("@lien.txt")

    def test_format_et_taille(self):
        with self.assertRaises(mentions.MentionError) as ctx:
            self.read("@logo.png")
        self.assertIn("format non pris en charge", str(ctx.exception))
        with mock.patch.object(attachments, "MAX_BYTES", 4), self.assertRaises(mentions.MentionError) as ctx:
            self.read("@README.md")
        self.assertIn("trop gros", str(ctx.exception))


class Api(Base):
    def setUp(self):
        super().setUp()
        self.http = client()
        for name, value in [("state", "ready"), ("model", mock.Mock(name="modele"))]:
            patcher = mock.patch.object(manager, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

        async def no_run(*args, **kwargs):
            yield "data: {}\n\n"
        patcher = mock.patch.object(agent, "run", no_run)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cid = new_conversation(self.root)

    def post(self, cid=None, **body):
        return self.http.post("/api/chat", json={"conversation_id": cid or self.cid, **body}, headers=ORIGIN)

    def test_propositions_d_une_conversation(self):
        r = self.http.get("/api/fichiers", params={"q": "app.py", "conversation_id": self.cid})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["files"][0]["path"], "src/app.py")

    def test_propositions_avant_la_creation_de_la_conversation(self):
        r = self.http.get("/api/fichiers", params={"q": "readme", "dossier": str(self.root)})
        self.assertEqual([f["path"] for f in r.json()["files"]], ["README.md"])

    def test_pas_de_dossier_pas_de_proposition(self):
        discussion = conversations.create()["id"]
        self.assertEqual(self.http.get("/api/fichiers", params={"conversation_id": discussion}).json(), {"files": []})
        self.assertEqual(self.http.get("/api/fichiers").json(), {"files": []})
        self.assertEqual(self.http.get("/api/fichiers", params={"conversation_id": "inconnue"}).status_code, 404)
        self.assertEqual(self.http.get("/api/fichiers", params={"dossier": str(self.root / "absent")}).status_code, 400)

    def test_mention_jointe_au_message(self):
        r = self.post(content="Explique @src/app.py")
        self.assertEqual(r.status_code, 200)
        message = conversations.get(self.cid)["messages"][-1]
        self.assertEqual(message["content"], "Explique @src/app.py")  # le texte tapé reste tel quel
        self.assertEqual([(f["name"], f["text"]) for f in message["files"]], [("src/app.py", "print('app')")])

    def test_mention_et_piece_jointe_comptent_ensemble(self):
        url = "data:text/plain;base64," + base64.b64encode(b"x").decode()
        files = [{"name": f"{i}.txt", "data": url} for i in range(5)]
        r = self.post(content="Lis @README.md", files=files)
        self.assertEqual(r.status_code, 400)
        self.assertIn("5 fichiers au plus", r.json()["detail"])

    def test_secret_refuse(self):
        r = self.post(content="Montre-moi @.env")
        self.assertEqual(r.status_code, 400)
        self.assertIn("ressemble à un secret", r.json()["detail"])
        self.assertEqual(conversations.get(self.cid)["messages"], [])  # rien n'a été enregistré

    def test_sans_dossier_attache_le_texte_reste_du_texte(self):
        discussion = conversations.create()["id"]
        r = self.post(cid=discussion, content="Lis @README.md")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(conversations.get(discussion)["messages"][-1].get("files"))


if __name__ == "__main__":
    unittest.main()
