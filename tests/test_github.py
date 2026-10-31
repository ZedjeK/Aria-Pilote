"""Git et GitHub : liste noire, commandes qui sortent du PC toujours demandées, branches protégées."""

import asyncio
import copy
import json
import subprocess
import unittest

from base import new_conversation, workspace

from aria_pilote import config, github, permissions, toolrun, tools


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
        with self.assertRaises(tools.ToolError) as ctx:
            tools.prepare_run_command(str(self.root), {"command": "git push origin main"})
        self.assertIn("branche protégée", str(ctx.exception))
        self.assertIn("pull request", str(ctx.exception))
        tools.prepare_run_command(str(self.root), {"command": "git push -u origin feat/x"})


if __name__ == "__main__":
    unittest.main()
