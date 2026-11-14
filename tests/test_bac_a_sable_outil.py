"""Bac à sable, outils de l'agent : copie du projet (fichiers, plafonds, archive), run_sandbox et sandbox_reset (faux
wsl.exe), disponibilité selon la conversation, route de la conversation et nettoyage. Le vrai essai ne tourne que sur
demande (ARIA_TEST_WSL=1) : il crée puis supprime une vraie distribution.
"""

import asyncio
import io
import os
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import types
import unittest
from unittest import mock

from base import ORIGIN, client, new_conversation, workspace
from test_bac_a_sable import FakeWsl

from aria_pilote import agent, bac_a_sable as bas, conversations, tools


def project(files):
    """Un dossier de projet dans le dossier de test, avec ces fichiers {chemin: texte}."""
    root = workspace("copie")
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))  # octets exacts : write_text ajouterait des CR sous Windows
    return root


class Fichiers(unittest.TestCase):
    def test_depot_git_ce_que_git_n_ignore_pas(self):
        if shutil.which("git") is None:
            self.skipTest("git absent")
        root = project({"a.py": "print(1)", "sub/b.txt": "b", ".gitignore": "ignore.txt\nbuild/\n", "ignore.txt": "x",
                        "build/x.o": "x", "node_modules/y.js": "y", "supprime.txt": "z", ".env": "SECRET=1"})
        (root / ".gitignore").write_text(".gitignore\nignore.txt\nbuild/\n.env\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "add", "supprime.txt"], cwd=root, check=True)
        (root / "supprime.txt").unlink()  # suivi, mais plus sur le disque
        # Ni ce que .gitignore écarte (dont un .env), ni node_modules, ni un fichier disparu
        self.assertEqual(set(bas.project_files(root)), {"a.py", "sub/b.txt"})

    def test_sans_git_parcours_habituel(self):
        root = project({"a.py": "1", "sub/b.txt": "b", ".venv/lib/x.py": "x", "node_modules/y.js": "y",
                        "sub/__pycache__/c.pyc": "c", ".git/config": "g"})
        self.assertEqual(set(bas.project_files(root)), {"a.py", "sub/b.txt"})

    def test_lien_jamais_suivi(self):
        root = project({"a.py": "1"})
        outside = project({"secret.txt": "hors du projet"})
        try:
            os.symlink(outside / "secret.txt", root / "lien.txt")
        except (OSError, NotImplementedError):
            self.skipTest("liens symboliques non permis ici")
        self.assertEqual(bas.project_files(root), ["a.py"])

    @unittest.skipUnless(sys.platform == "win32", "jonctions Windows")
    def test_jonction_jamais_suivie(self):
        root = project({"a.py": "1"})
        outside = project({"secret.txt": "hors du projet"})
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(root / "sortie"), str(outside)], capture_output=True)
        self.assertEqual(made.returncode, 0, made.stdout)
        self.addCleanup(os.rmdir, root / "sortie")  # retire la jonction, pas ce qu'elle désigne
        self.assertEqual(bas.project_files(root), ["a.py"])

    def test_plafond_de_fichiers_et_de_taille(self):
        root = project({"petit.txt": "x", "gros/un.bin": "0" * 3_000_000, "gros/deux.bin": "0" * 3_000_000})
        with mock.patch.object(bas, "MAX_COPY_FILES", 2):
            with self.assertRaises(bas.BacASableError) as ctx:
                bas.project_files(root)
        self.assertIn("trop gros", str(ctx.exception))
        with mock.patch.object(bas, "MAX_COPY_BYTES", 1_000_000):
            with self.assertRaises(bas.BacASableError) as ctx:
                bas.project_files(root)
        self.assertIn("gros (6 Mo)", str(ctx.exception))  # ce qui pèse est nommé
        self.assertIn(".gitignore", str(ctx.exception))
        self.assertEqual(len(bas.project_files(root)), 3)  # sous les plafonds : tout passe

    def test_archive_proprietaire_et_droits(self):
        root = project({"a.py": "print(1)\n", "run.sh": "#!/bin/sh\necho ok\n", "sub/b.txt": "bonjour é"})
        data = bas.build_archive(root, ["a.py", "run.sh", "sub/b.txt"])
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            members = {m.name: m for m in tar.getmembers()}
            self.assertEqual(set(members), {"a.py", "run.sh", "sub/b.txt"})
            self.assertTrue(all(m.uid == bas.AGENT_UID and m.gid == bas.AGENT_UID for m in members.values()))
            self.assertEqual(members["a.py"].mode, 0o644)
            self.assertEqual(members["run.sh"].mode, 0o755)  # commence par « #! »
            self.assertEqual(tar.extractfile("sub/b.txt").read().decode("utf-8"), "bonjour é")

    def test_dossier_d_une_conversation(self):
        self.assertEqual(bas.project_dir("0123456789ab"), "/home/agent/projet-0123456789ab")
        for bad in ("../../etc", "abc", "0123456789AB", "0123456789ab/..", ""):
            with self.assertRaises(bas.BacASableError, msg=bad):
                bas.project_dir(bad)


class FakeProjectWsl(FakeWsl):
    """Le faux wsl.exe du bac à sable, avec la copie de projet : dossiers, archive reçue, commandes lancées."""

    def __init__(self):
        super().__init__()
        self.distros.append(bas.DISTRO)
        self.dirs, self.tars, self.runs, self.kills, self.removed = set(), [], [], [], []
        self.result = (0, "")
        self.release = threading.Event()
        self.block = False

    def __call__(self, args, stdin=None, timeout=None):
        base = ["-d", bas.DISTRO]
        if args[:4] == base + ["-u", bas.USER]:
            rest = args[4:]
            if rest[:3] == ["--exec", "test", "-d"]:
                self.calls.append(list(args))
                return (0, "") if rest[3] in self.dirs else (1, "")
            if rest[:3] == ["--exec", "sh", "-c"] and "tar -xf -" in rest[3]:
                self.calls.append(list(args))
                with tarfile.open(fileobj=io.BytesIO(stdin)) as tar:
                    self.tars.append({m.name: tar.extractfile(m).read().decode("utf-8") for m in tar.getmembers()})
                self.dirs.add(rest[-1])
                return 0, ""
            if rest[:2] == ["--cd", rest[1]] and rest[2:5] == ["--exec", "sh", "-c"]:
                self.calls.append(list(args))
                self.runs.append({"cd": rest[1], "script": rest[5], "command": rest[7], "timeout": rest[8],
                                  "wsl_timeout": timeout})
                if self.block:
                    self.release.wait(5)
                return self.result
        if args[:4] == base + ["-u", "root"]:
            rest = args[4:]
            if rest[:3] == ["--exec", "sh", "-c"] and rest[3] == bas.KILL_LEFTOVERS:
                self.calls.append(list(args))
                self.kills.append(rest[-1])
                self.release.set()
                return 0, ""
            if rest[:3] == ["--exec", "sh", "-c"] and rest[3] == 'rm -rf -- "$1"':
                self.calls.append(list(args))
                self.removed.append(rest[-1])
                self.dirs.discard(rest[-1])
                return 0, ""
        return super().__call__(args, stdin, timeout)


class Base(unittest.TestCase):
    def setUp(self):
        self.fake = FakeProjectWsl()
        self.original = bas.run_wsl
        bas.run_wsl = self.fake
        bas.forget_cache()
        self.addCleanup(self.restore)
        self.root = project({"a.py": "print(1)\n", "sub/b.txt": "b"})
        self.cid = new_conversation(self.root)
        self.target = bas.project_dir(self.cid)

    def restore(self):
        bas.run_wsl = self.original
        bas.forget_cache()

    def run_tool(self, name, **args):
        tool = tools.TOOLS[name]
        prepared = tool.prepare(str(self.root), args)
        return asyncio.run(tool.run(prepared, self.cid))


class Outils(Base):
    def test_premier_appel_copie_puis_lance(self):
        self.fake.result = (0, "bonjour")
        result = self.run_tool("run_sandbox", command="python3 a.py")
        self.assertEqual(result["status"], "ok")
        self.assertIn("Projet copié dans le bac à sable (2 fichiers", result["output"])
        self.assertIn("Code de sortie : 0", result["output"])
        self.assertIn("bonjour", result["output"])
        self.assertEqual(self.fake.tars, [{"a.py": "print(1)\n", "sub/b.txt": "b"}])
        self.assertEqual(self.fake.runs[0]["cd"], self.target)
        # Deuxième appel : la copie est là, rien n'est recopié
        result = self.run_tool("run_sandbox", command="ls")
        self.assertNotIn("Projet copié", result["output"])
        self.assertEqual(len(self.fake.tars), 1)

    def test_la_commande_va_telle_quelle(self):
        command = "echo \"a $HOME\"\nfor i in 1 2; do echo 'é'; done"
        self.run_tool("run_sandbox", command=command)
        run = self.fake.runs[0]
        self.assertEqual((run["script"], run["command"], run["timeout"]), (bas.RUN_SCRIPT, command, "60"))
        self.assertEqual(run["wsl_timeout"], 75)  # plus large que le délai : timeout tue d'abord
        # Le verrou réseau passe avant la commande de l'agent
        load = next(i for i, c in enumerate(self.fake.calls) if c[4:] == ["--exec", *bas.NFT_LOAD])
        ran = next(i for i, c in enumerate(self.fake.calls) if "--cd" in c)
        self.assertLess(load, ran)

    def test_code_non_nul_est_une_erreur(self):
        self.fake.result = (3, "boum")
        result = self.run_tool("run_sandbox", command="exit 3")
        self.assertEqual(result["status"], "error")
        self.assertIn("Code de sortie : 3", result["output"])

    def test_delai_depasse(self):
        self.run_tool("run_sandbox", command="echo copie")  # crée la copie
        self.fake.result = (137, "Killed")
        clock = types.SimpleNamespace(monotonic=iter([0, 61]).__next__)
        with mock.patch.object(bas, "time", clock):
            result = bas.run_in_project(self.cid, "sleep 90", 60)
        self.assertTrue(result["delai"])
        # Une commande qui sort d'elle-même avec 137, mais vite, n'est pas un délai
        clock = types.SimpleNamespace(monotonic=iter([0, 0.5]).__next__)
        with mock.patch.object(bas, "time", clock):
            self.assertFalse(bas.run_in_project(self.cid, "exit 137", 60)["delai"])
        # Alpine rend 9
        self.fake.result = (9, "Killed")
        clock = types.SimpleNamespace(monotonic=iter([0, 60]).__next__)
        with mock.patch.object(bas, "time", clock):
            self.assertTrue(bas.run_in_project(self.cid, "sleep 90", 60)["delai"])

    def test_sortie_delai_dans_l_outil(self):
        self.fake.result = (None, "wsl.exe n'a pas répondu en 75 s")
        result = self.run_tool("run_sandbox", command="sleep 900", timeout=60)
        self.assertEqual(result["status"], "error")
        self.assertIn("Délai de 60 s dépassé", result["output"])

    def test_arguments(self):
        for bad in ({"command": ""}, {"command": "   "}, {"command": "x" * (bas.MAX_COMMAND_CHARS + 1)},
                    {"command": "ls", "timeout": "abc"}):
            with self.assertRaises(tools.ToolError, msg=bad):
                tools.prepare_run_sandbox(str(self.root), bad)
        self.assertEqual(tools.prepare_run_sandbox(str(self.root), {"command": "ls", "timeout": 99999})["timeout"], 600)
        self.assertEqual(tools.prepare_run_sandbox(str(self.root), {"command": "ls", "timeout": -5})["timeout"], 1)
        self.assertEqual(tools.prepare_run_sandbox(str(self.root), {"command": "ls"})["timeout"], 60)

    def test_reset_refait_la_copie_a_neuf(self):
        self.run_tool("run_sandbox", command="touch nouveau")
        (self.root / "c.txt").write_bytes("ajouté côté Windows".encode("utf-8"))
        result = self.run_tool("sandbox_reset")
        self.assertIn("3 fichiers", result["output"])
        self.assertIn("effacé", result["output"])
        self.assertEqual(self.fake.kills, [self.target])  # ce qui tournait encore est tué d'abord
        self.assertEqual(self.fake.tars[-1]["c.txt"], "ajouté côté Windows")

    def test_sans_bac_a_sable(self):
        self.fake.distros.clear()
        with self.assertRaises(tools.ToolError) as ctx:
            self.run_tool("run_sandbox", command="ls")
        self.assertIn("n'existe pas", str(ctx.exception))

    def test_stop_arrete_la_commande(self):
        self.run_tool("run_sandbox", command="echo copie")
        self.fake.block = True  # la commande ne rend pas la main toute seule
        prepared = tools.prepare_run_sandbox(str(self.root), {"command": "sleep 500"})

        async def scenario():
            task = asyncio.create_task(tools.run_run_sandbox(prepared, self.cid))
            await asyncio.sleep(0.3)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        asyncio.run(scenario())
        self.assertEqual(self.fake.kills, [self.target])

    def test_projet_trop_gros_refuse_net(self):
        with mock.patch.object(bas, "MAX_COPY_FILES", 1):
            with self.assertRaises(tools.ToolError) as ctx:
                self.run_tool("run_sandbox", command="ls")
        self.assertIn("trop gros", str(ctx.exception))
        self.assertEqual(self.fake.tars, [])  # rien n'est parti vers la distribution


class Disponibilite(Base):
    def conv(self, **extra):
        return {"id": self.cid, "messages": [], **extra}

    def names(self, conv, root):
        return agent.task_tools(conv, root)[0]

    def test_outils_seulement_si_tout_est_la(self):
        root = str(self.root)
        for name in ("run_sandbox", "sandbox_reset"):
            self.assertIn(name, self.names(self.conv(sandbox=True), root))
            self.assertNotIn(name, self.names(self.conv(), root))  # option coupée par défaut
            self.assertNotIn(name, self.names(self.conv(sandbox=False), root))
            self.assertNotIn(name, self.names(self.conv(sandbox=True), None))  # sans dossier, rien à copier
        self.fake.distros.clear()
        bas.forget_cache()
        self.assertNotIn("run_sandbox", self.names(self.conv(sandbox=True), root))  # bac à sable absent

    def test_coupe_aucun_appel_a_wsl(self):
        self.names(self.conv(), str(self.root))
        self.assertEqual(self.fake.calls, [])  # option coupée : WSL n'est même pas interrogé

    def test_verrou_du_plan(self):
        for name in ("run_sandbox", "sandbox_reset"):
            self.assertEqual(agent.tool_kind(name), "sandbox")
            self.assertIn("sandbox", agent.LOCKED_KINDS)

    def test_regle_du_prompt(self):
        root = str(self.root)
        on = agent._system_prompt(self.conv(sandbox=True), root, None)
        self.assertIn("Bac à sable Linux", on)
        self.assertIn("Il n'a pas de réseau", on)
        self.assertNotIn("Bac à sable Linux", agent._system_prompt(self.conv(), root, None))
        self.addCleanup(bas.config.update, {"bac_a_sable": {"reseau_public": False}})
        bas.config.update({"bac_a_sable": {"reseau_public": True}})
        self.assertIn("pas le réseau local", agent._system_prompt(self.conv(sandbox=True), root, None))

    def test_cache_de_disponibilite(self):
        bas.forget_cache()
        self.assertTrue(bas.available())
        listings = lambda: sum(1 for c in self.fake.calls if c == ["--list", "--quiet"])  # noqa: E731
        before = listings()
        for _ in range(5):
            bas.available()
        self.assertEqual(listings(), before)  # gardé quelques secondes
        self.assertTrue(bas.available(force=True))
        self.assertEqual(listings(), before + 1)


class Routes(Base):
    def setUp(self):
        super().setUp()
        self.client = client()

    def patch(self, **body):
        return self.client.patch(f"/api/conversations/{self.cid}", json=body, headers=ORIGIN)

    def test_activer_demande_un_dossier_et_un_bac_a_sable(self):
        no_folder = new_conversation()
        r = self.client.patch(f"/api/conversations/{no_folder}", json={"sandbox": True}, headers=ORIGIN)
        self.assertEqual(r.status_code, 400)
        self.fake.distros.clear()
        r = self.patch(sandbox=True)
        self.assertEqual(r.status_code, 409)
        self.assertIn("n'existe pas", r.json()["detail"])
        self.assertNotIn("sandbox", conversations.get(self.cid))
        self.fake.distros.append(bas.DISTRO)
        self.assertEqual(self.patch(sandbox=True).status_code, 200)
        self.assertTrue(conversations.get(self.cid)["sandbox"])
        self.fake.distros.clear()
        self.assertEqual(self.patch(sandbox=False).status_code, 200)  # couper ne demande rien
        self.assertFalse(conversations.get(self.cid)["sandbox"])

    def test_supprimer_la_conversation_efface_sa_copie(self):
        self.assertEqual(self.patch(sandbox=True).status_code, 200)
        self.run_tool("run_sandbox", command="ls")
        self.assertIn(self.target, self.fake.dirs)
        self.assertEqual(self.client.delete(f"/api/conversations/{self.cid}", headers=ORIGIN).status_code, 200)
        self.assertEqual(self.fake.removed, [self.target])
        self.assertNotIn(self.target, self.fake.dirs)

    def test_sans_bac_a_sable_supprimer_ne_reveille_pas_wsl(self):
        self.client.delete(f"/api/conversations/{self.cid}", headers=ORIGIN)  # jamais activé
        self.assertEqual(self.fake.calls, [])


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

    def leftovers(self, word):
        code, out = bas.run_wsl(["-d", bas.DISTRO, "-u", "root", "--exec", "sh", "-c",
                                 "n=0; for f in /proc/[0-9]*/cmdline; do c=$(tr '\\0' ' ' < \"$f\" 2>/dev/null); "
                                 f"case \"$c\" in '{word}'*) n=$((n+1));; esac; done; echo $n"])
        return int(out.strip() or 0)

    def test_copie_commandes_delai_reset(self):
        root = project({"hello.py": "print('salut')\n", "run.sh": "#!/bin/sh\necho depuis-run\n", "sub/b.txt": "b\n"})
        other = project({"autre.txt": "autre projet\n"})
        bas.create("alpine")
        a, b = "0123456789ab", "ba9876543210"
        info = bas.copy_project(a, root)
        self.assertEqual(info["fichiers"], 3)
        bas.copy_project(b, other)
        # Commandes : dossier courant, fichiers, droits, code de sortie
        result = bas.run_in_project(a, "pwd; ls; ./run.sh; exit 4")
        self.assertEqual(result["code"], 4, result)
        self.assertEqual(result["sortie"].splitlines(), [bas.project_dir(a), "hello.py", "run.sh", "sub",
                                                         "depuis-run"])
        self.assertEqual(bas.run_in_project(b, "ls")["sortie"], "autre.txt")  # chaque conversation a sa copie
        # Pas de réseau, et la copie reste à l'agent
        code, out = bas.run_wsl(["-d", bas.DISTRO, "-u", "agent", "--exec", "sh", "-c",
                                 "wget -q -T 4 -O /dev/null https://dl-cdn.alpinelinux.org/ >/dev/null 2>&1 "
                                 "&& echo ouvert || echo ferme"])
        self.assertEqual(out, "ferme")
        # Délai : la commande et ses enfants sont arrêtés, rien ne reste
        started = time.monotonic()
        result = bas.run_in_project(a, "sleep 311 & sleep 312 & wait", 2)
        self.assertTrue(result["delai"], result)
        self.assertLess(time.monotonic() - started, 12)
        time.sleep(1)
        self.assertEqual(self.leftovers("sleep 31"), 0)
        # Un processus qui quitte son groupe survit à la commande, mais pas à reset ; l'autre copie n'est pas touchée
        bas.run_in_project(a, "setsid sh -c 'sleep 313' >/dev/null 2>&1 & sleep 0.2", 5)
        bas.run_in_project(b, "setsid sh -c 'sleep 314' >/dev/null 2>&1 & sleep 0.2", 5)
        self.assertEqual(self.leftovers("sleep 31"), 2)
        bas.run_in_project(a, "echo modifie > hello.py")
        (root / "nouveau.txt").write_text("vu après reset\n", encoding="utf-8")
        bas.reset_project(a, root)
        time.sleep(1)
        self.assertEqual(self.leftovers("sleep 313"), 0)
        self.assertEqual(self.leftovers("sleep 314"), 1)
        self.assertEqual(bas.run_in_project(a, "cat hello.py; cat nouveau.txt")["sortie"].splitlines(),
                         ["print('salut')", "vu après reset"])
        # Stop : tout ce qui tourne dans la copie de la conversation est tué
        bas.run_in_project(a, "setsid sh -c 'sleep 315' >/dev/null 2>&1 & sleep 0.2", 5)
        bas.stop_commands(a)
        time.sleep(1)
        self.assertEqual(self.leftovers("sleep 315"), 0)
        # Suppression d'une conversation : sa copie part, l'autre reste
        self.assertTrue(bas.forget_project(a))
        self.assertFalse(bas.has_project(a))
        self.assertTrue(bas.has_project(b))


if __name__ == "__main__":
    unittest.main()
