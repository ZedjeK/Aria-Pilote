"""Git et GitHub : liste noire, commandes qui sortent du PC toujours demandées, branches protégées, Réglages › GitHub."""

import asyncio
import copy
import json
import subprocess
import unittest

from unittest import mock

from base import ORIGIN, client, new_conversation, workspace

from aria_pilote import config, github, permissions, skills, toolrun, tools


def keep_config(test):
    saved = copy.deepcopy(config.get())

    def restore():
        with config._lock:
            config._data = saved
            config._save_locked()
    test.addCleanup(restore)


def git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


class ListeNoire(unittest.TestCase):
    def test_refusees(self):
        for command, why in [
            ("git push --force", "push forcé"), ("git push -f origin feat", "push forcé"),
            ("git push -uf origin x", "push forcé"), ("git push origin +main", "push forcé"),
            ("git push --mirror", "push forcé"), ("git -C projet push --force", "push forcé"),
            ("git push origin --delete feat", "suppression de branches"), ("git push origin :feat", "suppression de branches"),
            ("git push --prune origin", "suppression de branches"),
            ("gh repo delete a/b --yes", "suppression sur GitHub"), ("gh release delete v1", "suppression sur GitHub"),
            ("gh -R a/b issue delete 3", "suppression sur GitHub"), ("gh repo deploy-key delete 1", "suppression sur GitHub"),
            ("gh release delete-asset v1 x.zip", "suppression sur GitHub"),
            ("gh api -X DELETE repos/a/b", "suppression sur GitHub"), ("gh api --method=delete x", "suppression sur GitHub"),
            ("gh auth token", "jeton"), ("gh auth status --show-token", "jeton"), ("git credential fill", "identifiants"),
            ("gh auth login", "à faire toi-même"), ("git status; gh auth logout", "à faire toi-même"),
        ]:
            with self.subTest(command=command):
                self.assertIn(why, tools.check_denylist(command) or "")

    def test_permises(self):
        for command in ["git push --force-with-lease", "git push origin HEAD:feat", "git push --follow-tags",
                        'gh pr create --title "Delete old files"', "gh issue list --search delete",
                        'git commit -m "on push -f ou --delete ?"', "gh auth status", "gh pr list"]:
            with self.subTest(command=command):
                self.assertIsNone(tools.check_denylist(command))


class Sortantes(unittest.TestCase):
    def test_classement(self):
        for command, expected in [
            ("git push", "git push"), ("git status; git push origin feat", "git push"),
            ("& 'C:\\Program Files\\Git\\cmd\\git.exe' push origin feat", "git push"),
            ("gh pr create --fill", "gh pr create"), ("gh pr merge 3 --squash", "gh pr merge"),
            ("gh -R a/b issue comment 3 -b ok", "gh issue comment"), ("gh release create v1", "gh release create"),
            ("gh api repos/a/b/issues -f title=x", "gh api (écriture)"), ("gh api -X PATCH repos/a/b", "gh api (écriture)"),
            ("gh workflow run ci.yml", "gh workflow run"),
            ("gh pr list", None), ("gh pr checks 12", None), ("gh pr view 3 --comments", None), ("gh pr checkout 5", None),
            ("gh run view 1 --log-failed", None), ("gh api repos/a/b/pulls", None), ("gh search issues bug", None),
            ("git log --oneline", None), ("git commit -m 'push'", None), ("gh --version", None),
        ]:
            with self.subTest(command=command):
                self.assertEqual(github.outward(command), expected)

    def test_jamais_de_regle(self):
        root = workspace()
        with self.assertRaises(permissions.RuleError) as ctx:
            permissions.add(root, "git push*", "git push origin feat")
        self.assertIn("toujours demandée", str(ctx.exception))
        with self.assertRaises(permissions.RuleError):
            permissions.add(root, "gh pr create --fill", "gh pr create --fill")
        permissions.add(root, "gh pr*", "gh pr list")  # une règle de lecture reste possible

    def test_redemandee_malgre_une_regle_large(self):
        root = workspace()
        cid = new_conversation(root)
        permissions.add(root, "git*", "git --version")

        async def go(command):
            out, evs = {}, []
            async for ev in toolrun.run_tool(cid, str(root), "run_command", {"command": command}, "c1", out):
                evs.append(json.loads(ev[6:]))
                if evs[-1]["type"] == "approval":
                    toolrun.resolve_approval(evs[-1]["approval_id"], "deny")
            return out["result"], evs

        result, evs = asyncio.run(go("git --version"))
        self.assertEqual((evs, result.get("rule")), ([], "git*"))
        result, evs = asyncio.run(go("git push origin feat/x"))
        approval = next(e for e in evs if e["type"] == "approval")
        self.assertEqual((approval["outward"], approval["rule_prefix"]), ("git push", None))
        self.assertEqual(result["status"], "denied")

    def test_allow_rule_refuse_par_le_serveur(self):
        root = workspace()
        cid = new_conversation(root)

        async def go():
            out = {}
            gen = toolrun.run_tool(cid, str(root), "run_command", {"command": "gh pr create --fill"}, "c1", out)
            ev = json.loads((await gen.__anext__())[6:])
            with self.assertRaises(toolrun.ApprovalError):
                toolrun.resolve_approval(ev["approval_id"], "allow_rule", "gh pr create --fill")
            toolrun.resolve_approval(ev["approval_id"], "deny")
            async for _ in gen:
                pass
        asyncio.run(go())


class BranchesProtegees(unittest.TestCase):
    def setUp(self):
        keep_config(self)
        self.root = workspace()
        git(self.root, "init", "-q", "-b", "main")

    def test_push_direct_refuse(self):
        for command in ["git push", "git push origin", "git push -u origin main", "git push origin HEAD:main",
                        "git push origin feat:refs/heads/main", "git push origin feat main", "git push --all origin",
                        "git status; git push origin master", "git push origin HEAD"]:
            with self.subTest(command=command):
                self.assertIsNotNone(github.check_push(command, self.root))

    def test_branche_de_travail_et_tags_permis(self):
        for command in ["git push -u origin feat/x", "git push origin HEAD:feat/x", "git push origin v0.23.1",
                        "git push --tags", "git log", "gh pr create --fill"]:
            with self.subTest(command=command):
                self.assertIsNone(github.check_push(command, self.root))
        git(self.root, "switch", "-q", "-c", "feat/x")
        self.assertIsNone(github.check_push("git push", self.root))
        self.assertIsNone(github.check_push("git push -u origin HEAD", self.root))

    def test_dossier_de_git_c(self):
        sub = self.root / "autre"
        sub.mkdir()
        git(sub, "init", "-q", "-b", "feat")
        git(self.root, "switch", "-q", "-c", "feat/y")  # le dossier de travail n'est plus sur main
        self.assertIsNone(github.check_push("git -C autre push", self.root))
        git(sub, "switch", "-q", "-c", "master")
        self.assertIsNotNone(github.check_push("git -C autre push", self.root))

    def test_liste_reglable(self):
        config.update({"github": {"protected_branches": ["prod"]}})
        self.assertIsNone(github.check_push("git push origin main", self.root))
        self.assertIsNotNone(github.check_push("git push origin prod", self.root))
        config.update({"github": {"protected_branches": []}})
        self.assertIsNone(github.check_push("git push origin prod", self.root))

    def test_outil_refuse_avec_explication(self):
        # Le banc l'a montré : « git add … && git commit … && git push origin main » refusé en entier, le modèle
        # croyait avoir commité. Le message dit que rien n'a été lancé, et comment s'en sortir.
        with self.assertRaises(tools.ToolError) as ctx:
            tools.prepare_run_command(str(self.root), {"command": "git add x && git commit -m y && git push origin master"})
        message = str(ctx.exception)
        for expected in ("une branche protégée", "Rien n'a été lancé", "git switch -c fix/sujet-court",
                         "git branch -f master origin/master"):
            self.assertIn(expected, message)
        tools.prepare_run_command(str(self.root), {"command": "git push -u origin feat/x"})

    def test_la_reparation_proposee_marche(self):
        # Commit déjà fait sur main : la branche créée là, puis main remise sur origin, comme le dit le message
        origin = workspace("origin")
        git(origin, "init", "-q", "--bare", "-b", "main")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@aria.invalid")
        git(self.root, "commit", "-q", "--allow-empty", "-m", "Départ")
        git(self.root, "remote", "add", "origin", str(origin))
        git(self.root, "push", "-q", "-u", "origin", "main")
        git(self.root, "commit", "-q", "--allow-empty", "-m", "Correctif")
        for command in ("switch -c fix/sujet-court", "push -q -u origin HEAD", "branch -f main origin/main"):
            git(self.root, *command.split())
        log = lambda ref: subprocess.run(["git", "log", "--format=%s", ref], cwd=self.root, capture_output=True,
                                         text=True, encoding="utf-8").stdout.split()
        self.assertEqual((log("main"), log("origin/fix/sujet-court")), (["Départ"], ["Correctif", "Départ"]))


GH_AUTH = """github.com
  ✓ Logged in to github.com account ZedjeK (keyring)
  - Active account: true
  - Git operations protocol: https
  - Token: gho_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789
  - Token scopes: 'gist', 'read:org', 'repo', 'workflow'
"""


class Reglages(unittest.TestCase):
    def setUp(self):
        keep_config(self)
        self.api = client()

    def put(self, **github_settings):
        return self.api.put("/api/config", json={"github": github_settings}, headers=ORIGIN)

    def test_valeurs_verifiees_et_normalisees(self):
        r = self.put(protected_branches="dev, prod  ,dev", attribution="Co-Authored-By: Aria\n<a@b.fr>",
                     merge_method="rebase", feature_prefix=" nouveau/ ")
        self.assertEqual(r.status_code, 200)
        self.assertEqual({k: r.json()["github"][k] for k in ("protected_branches", "attribution", "merge_method",
                                                             "feature_prefix")},
                         {"protected_branches": ["dev", "prod"], "attribution": "Co-Authored-By: Aria <a@b.fr>",
                          "merge_method": "rebase", "feature_prefix": "nouveau/"})
        self.assertEqual(github.protected_branches(), ["dev", "prod"])
        for bad in ({"protected_branches": "main;calc"}, {"protected_branches": "-f"}, {"merge_method": "fast"},
                    {"fix_prefix": "a b"}):
            with self.subTest(bad=bad):
                self.assertEqual(self.put(**bad).status_code, 400)
        self.assertEqual(self.put(protected_branches="").json()["github"]["protected_branches"], [])

    def test_etat_sans_jamais_le_jeton(self):
        def fake_run(args, timeout=15):
            if args[1:] == ["--version"]:
                return 0, "gh version 2.103.3 (2026-10-28)\n" if "gh" in args[0] else "git version 2.56.0\n"
            if args[1:3] == ["auth", "status"]:
                return 0, GH_AUTH
            return 0, {"user.name": "ZedjeK\n", "user.email": "z@exemple.fr\n"}[args[-1]]
        with mock.patch.object(github.shutil, "which", side_effect=lambda name: f"C:/bin/{name}.exe"), \
                mock.patch.object(github, "_run", side_effect=fake_run):
            r = self.api.get("/api/github/status")
        data = r.json()
        self.assertEqual((data["git"]["user_name"], data["gh"]["version"], data["gh"]["accounts"], data["gh"]["scopes"]),
                         ("ZedjeK", "2.103.3", [{"host": "github.com", "login": "ZedjeK"}],
                          ["gist", "read:org", "repo", "workflow"]))
        self.assertNotIn("gho_", r.text)
        with mock.patch.object(github.shutil, "which", return_value=None):
            self.assertEqual(self.api.get("/api/github/status").json(), {"git": None, "gh": None})

    def test_competences_coupees(self):
        self.assertTrue(set(github.SKILLS) <= set(skills.discover()))
        config.update({"github": {"skills": False}})
        self.assertFalse(set(github.SKILLS) & set(skills.discover()))
        self.assertNotIn("github-pr", skills.prompt_section())
        with self.assertRaises(tools.ToolError):
            tools.prepare_load_skill(None, {"name": "github-pr"})

    def test_conventions_ajoutees_a_la_competence(self):
        config.update({"github": {"merge_method": "merge", "attribution": "Co-Authored-By: Aria <a@b.fr>",
                                  "protected_branches": ["prod"]}})
        text = tools.prepare_load_skill(None, {"name": "github-pr"})["output"]
        self.assertIn("gh pr merge <n> --merge", text)
        self.assertIn("telle quelle : Co-Authored-By: Aria <a@b.fr>", text)
        self.assertIn("(Aria le refuse) : prod.", text)
        self.assertNotIn("Réglages › GitHub", tools.prepare_load_skill(None, {"name": "dev"})["output"])


class LecturesSansDemander(unittest.TestCase):
    def setUp(self):
        keep_config(self)

    def test_classement(self):
        for command, expected in [
            ("git status", True), ("git log --oneline -5", True), ("git diff HEAD~1", True), ("git fetch origin", True),
            ("git branch -a", True), ("git tag", True), ("gh pr list", True), ("gh pr checks 12", True),
            ("gh run view 5 --log-failed", True), ("gh search issues bug", True), ("gh -R a/b issue view 3", True),
            ("git -c core.pager=calc log", False), ("git branch nouvelle", False), ("git branch -D x", False),
            ("git tag v1", False), ("git fetch origin main:main", False), ("git fetch --upload-pack=calc", False),
            ("git diff --output=x", False), ("git status; Remove-Item x", False), ("git status | Out-File x", False),
            ("git push", False), ("gh pr create", False), ("gh pr view 3 --web", False), ("gh api repos/a/b", False),
            ("gh pr checkout 5", False), ("python -m unittest", False),
        ]:
            with self.subTest(command=command):
                self.assertEqual(github.read_only(command), expected)

    def run_command(self, root, command):
        cid = new_conversation(root)

        async def go():
            out, evs = {}, []
            async for ev in toolrun.run_tool(cid, str(root), "run_command", {"command": command}, "c1", out):
                evs.append(json.loads(ev[6:]))
                if evs[-1]["type"] == "approval":
                    toolrun.resolve_approval(evs[-1]["approval_id"], "deny")
            return out["result"], evs
        return asyncio.run(go())

    def test_seulement_si_active(self):
        root = workspace()
        git(root, "init", "-q", "-b", "main")
        _, evs = self.run_command(root, "git status --short")
        self.assertTrue(any(e["type"] == "approval" for e in evs))
        config.update({"github": {"read_auto": True}})
        result, evs = self.run_command(root, "git status --short")
        self.assertEqual(evs, [])
        self.assertEqual((result["status"], result["rule"]),
                         ("ok", "lecture git ou GitHub, autorisée d'office (Réglages › GitHub)"))
        _, evs = self.run_command(root, "git status --short; echo x")
        self.assertTrue(any(e["type"] == "approval" for e in evs))


if __name__ == "__main__":
    unittest.main()
