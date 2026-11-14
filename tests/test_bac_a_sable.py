"""Bac à sable Linux : détection de WSL, image vérifiée, archive cloisonnée avant l'import, création, retour arrière
et suppression. Un faux wsl.exe joue WSL ; le vrai essai ne tourne que sur demande (ARIA_TEST_WSL=1), car il crée
puis supprime une vraie distribution.
"""

import http.server
import io
import os
import shutil
import sys
import tarfile
import threading
import unittest

from base import HOME

from aria_pilote import bac_a_sable as bas


def image_tar(path, wsl_conf=None):
    """Une petite image : quelques dossiers, un fichier, un lien, et éventuellement son propre /etc/wsl.conf."""
    with tarfile.open(path, "w:gz") as tar:
        for d in ("etc", "bin", "home"):
            info = tarfile.TarInfo(f"./{d}")
            info.type, info.mode = tarfile.DIRTYPE, 0o755
            tar.addfile(info)
        data = b"NAME=Essai\n"
        info = tarfile.TarInfo("./etc/os-release")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("./bin/sh")
        link.type, link.linkname = tarfile.SYMTYPE, "busybox"
        tar.addfile(link)
        if wsl_conf is not None:
            data = wsl_conf.encode()
            info = tarfile.TarInfo("./etc/wsl.conf")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


class FakeWsl:
    """Joue wsl.exe : distributions inscrites, et chaque appel noté. L'archive est lue au moment de l'import."""

    def __init__(self, installed=True, setup_code=0):
        self.installed, self.setup_code = installed, setup_code
        self.distros, self.calls, self.imported = [], [], None
        self.mode, self.nft_code, self.loaded, self.keep_listing = "nat", 0, [], True

    def __call__(self, args, stdin=None, timeout=None):
        self.calls.append(list(args))
        if not self.installed:
            return None, "wsl.exe introuvable"
        if args == ["--status"]:
            return 0, "Version par défaut : 2"
        if args == ["--list", "--quiet"]:
            return (0, "\n".join(self.distros)) if self.distros else (1, "aucune distribution installée")
        if args[0] == "--import":
            with tarfile.open(args[3]) as tar:
                self.imported = {m.name: (tar.extractfile(m).read().decode() if m.isreg() else m.type)
                                 for m in tar.getmembers()}
            self.distros.append(args[1])
            return 0, "Opération réussie."
        if args[:4] == ["-d", bas.DISTRO, "-u", "root"]:
            return self.as_root(args[4:], stdin)
        if args[:4] == ["-d", bas.DISTRO, "-u", bas.USER]:
            return 0, "commande de l'agent"
        if args[0] == "--terminate":
            return 0, ""
        if args[0] == "--unregister":
            self.distros.remove(args[1]) if args[1] in self.distros else None
            return 0, "Désinscription."
        return 0, ""

    def as_root(self, rest, stdin):
        if rest == ["--exec", "sh", "-c", bas.SETUP]:
            return self.setup_code, "" if self.setup_code == 0 else "useradd: erreur"
        if rest == ["--exec", "sh", "-c", "wslinfo --networking-mode 2>/dev/null || echo inconnu"]:
            return 0, self.mode
        if rest == ["--exec", "cat", "/etc/resolv.conf"]:
            return 0, "# généré par WSL\nnameserver 10.255.255.254\n"
        if rest == ["--exec", *bas.NFT_LOAD]:
            self.loaded.append(stdin.decode("utf-8"))
            return self.nft_code, "" if self.nft_code == 0 else "Error: syntax"
        if rest == ["--exec", *bas.NFT_LIST]:
            if not (self.loaded and self.keep_listing):
                return 1, "Error: No such file or directory"
            return 0, self.loaded[-1].replace("counter reject", "counter packets 0 bytes 0 reject")
        if rest == ["--exec", *bas.NFT_DELETE]:
            return 0, ""
        return 0, ""


class Creation(unittest.TestCase):
    def setUp(self):
        self.fake = FakeWsl()
        self.original = bas.run_wsl, bas.download
        bas.run_wsl = self.fake
        self.image = image_tar(HOME / "image-essai.tar.gz", wsl_conf="[automount]\nenabled = true\n")
        bas.download = lambda key, progress=None: self.image
        self.addCleanup(self.restore)

    def restore(self):
        bas.run_wsl, bas.download = self.original
        shutil.rmtree(bas.BASE, ignore_errors=True)

    def test_sans_wsl(self):
        self.fake.installed = False
        self.assertEqual(bas.status()["wsl"], False)
        with self.assertRaises(bas.BacASableError) as ctx:
            bas.create()
        self.assertIn("wsl --install", str(ctx.exception))  # il l'installe lui-même : Aria ne fait que détecter

    def test_image_inconnue(self):
        with self.assertRaises(bas.BacASableError):
            bas.create("arch")

    def test_creation_cloisonnee_des_l_import(self):
        state = bas.create("debian")
        self.assertTrue(state["cree"])
        self.assertEqual(state["image"]["image"], "debian")
        imported = self.fake.imported
        # Notre wsl.conf remplace celui de l'image, et rien d'autre ne manque
        self.assertEqual(imported["etc/wsl.conf"], bas.WSL_CONF)
        self.assertNotIn("./etc/wsl.conf", imported)
        self.assertIn("enabled = false", imported["etc/wsl.conf"])
        self.assertIn("appendWindowsPath = false", imported["etc/wsl.conf"])
        self.assertEqual(imported["./etc/os-release"], "NAME=Essai\n")
        self.assertEqual(imported["./bin/sh"], tarfile.SYMTYPE)
        calls = self.fake.calls
        importing = next(c for c in calls if c[0] == "--import")
        self.assertEqual(importing[1:3], [bas.DISTRO, str(bas.DISK_DIR)])
        self.assertEqual(importing[-2:], ["--version", "2"])
        setup = next(c for c in calls if c[:4] == ["-d", bas.DISTRO, "-u", "root"])
        self.assertEqual(setup[-1], bas.SETUP)
        self.assertEqual(setup[4], "--exec")  # pas de shell intermédiaire qui développerait $g
        self.assertGreater(calls.index(["--terminate", bas.DISTRO]), calls.index(setup))  # wsl.conf appliqué ensuite
        self.assertFalse((bas.IMAGES_DIR / "prepare.tar").exists())  # l'archive préparée ne traîne pas

    def test_deja_cree(self):
        bas.create()
        with self.assertRaises(bas.BacASableError):
            bas.create()

    def test_preparation_ratee_rien_de_bancal(self):
        self.fake.setup_code = 1
        with self.assertRaises(bas.BacASableError) as ctx:
            bas.create()
        self.assertIn("useradd", str(ctx.exception))
        self.assertIn(["--unregister", bas.DISTRO], self.fake.calls)
        self.assertFalse(bas.status()["cree"])
        self.assertFalse(bas.STATE_FILE.exists())

    def test_suppression(self):
        bas.create()
        bas.IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        (bas.IMAGES_DIR / "gardee.tar.gz").write_text("x")
        state = bas.delete()
        self.assertFalse(state["cree"])
        self.assertIn(["--unregister", bas.DISTRO], self.fake.calls)
        self.assertFalse(bas.DISK_DIR.exists())
        self.assertFalse(bas.STATE_FILE.exists())
        self.assertTrue((bas.IMAGES_DIR / "gardee.tar.gz").exists())  # recréer sans retélécharger

    def test_suppression_retire_la_regle_de_la_machine_virtuelle(self):
        bas.create()
        bas.delete()
        delete_rule = ["-d", bas.DISTRO, "-u", "root", "--exec", *bas.NFT_DELETE]
        self.assertIn(delete_rule, self.fake.calls)
        self.assertLess(self.fake.calls.index(delete_rule), self.fake.calls.index(["--unregister", bas.DISTRO]))

    def test_preparation_sans_sudo_ni_mot_de_passe(self):
        self.assertIn(f"passwd -l {bas.USER}", bas.SETUP)
        self.assertIn("gpasswd -d", bas.SETUP)
        self.assertNotIn("sudoers", bas.SETUP)
        self.assertIn(f"default = {bas.USER}", bas.SETUP)

    def test_preparation_coupe_le_reseau_de_l_agent(self):
        self.assertIn(f"--uid {bas.AGENT_UID}", bas.SETUP)  # Debian
        self.assertIn(f"-u {bas.AGENT_UID}", bas.SETUP)  # Alpine
        self.assertIn("apk add --no-cache nftables", bas.SETUP)
        self.assertIn(bas.STRICT_RULES, bas.SETUP)
        self.assertIn(f"nft -f {bas.RULES_FILE}", bas.SETUP)
        self.assertIn(f"command = NFT -f {bas.RULES_FILE}", bas.SETUP)  # rechargée à chaque démarrage…
        self.assertIn('sed "s#NFT#$(command -v nft)#"', bas.SETUP)  # …par le chemin complet de nft
        self.assertIn("exit 1", bas.SETUP)  # sans nftables, pas de bac à sable

    def test_images_epinglees(self):
        for key, spec in bas.IMAGES.items():
            self.assertTrue(spec["url"].startswith("https://"), key)
            self.assertRegex(spec["sha256"], r"^[0-9a-f]{64}$", key)
            self.assertNotIn("latest", spec["url"], key)  # une version précise, jamais « la dernière »


class Regles(unittest.TestCase):
    def test_stricte_rien_pour_l_agent(self):
        lines = [line.strip() for line in bas.STRICT_RULES.splitlines()]
        self.assertIn(f"meta skuid {bas.AGENT_UID} counter reject", lines)
        self.assertNotIn("accept", "\n".join(lines[lines.index("type filter hook output priority 0; policy accept;") + 1:]))

    def test_se_remplacent_au_lieu_de_s_empiler(self):
        lines = bas.STRICT_RULES.splitlines()
        self.assertEqual(lines[:3], ["table inet aria_bac_a_sable {}", "delete table inet aria_bac_a_sable",
                                     "table inet aria_bac_a_sable {"])

    def test_boucle_locale(self):
        text = bas.rules(loopback=True)
        self.assertIn("ip daddr 127.0.0.0/8 accept", text)
        self.assertIn("ip6 daddr ::1 accept", text)
        self.assertNotIn(f"meta skuid {bas.AGENT_UID} accept", text)  # rien d'autre

    def test_internet_public_jamais_le_reseau_local(self):
        text = bas.rules(public=True, dns=["10.255.255.254", "fd00::1"])
        dns = text.index("10.255.255.254 udp dport 53 accept")
        private = text.index("172.16.0.0/12")
        self.assertLess(dns, private)  # le résolveur passe avant le refus des adresses privées
        self.assertLess(private, text.index(f"meta skuid {bas.AGENT_UID} accept"))
        for block in ("10.0.0.0/8", "192.168.0.0/16", "169.254.0.0/16", "127.0.0.0/8", "fc00::/7", "fe80::/10"):
            self.assertIn(block, text)
        self.assertIn("ip6 daddr fd00::1 tcp dport 53 accept", text)
        with self.assertRaises(ValueError):
            bas.rules(public=True, dns=["10.0.0.1; flush ruleset"])  # rien d'injecté depuis resolv.conf


class Verrou(unittest.TestCase):
    def setUp(self):
        self.fake = FakeWsl()
        self.fake.distros.append(bas.DISTRO)
        self.original = bas.run_wsl
        bas.run_wsl = self.fake
        self.addCleanup(setattr, bas, "run_wsl", self.original)
        self.addCleanup(bas.config.update, {"bac_a_sable": {"reseau_public": False}})

    def test_commande_de_l_agent_apres_le_verrou(self):
        self.assertEqual(bas.run("ls"), (0, "commande de l'agent"))
        names = [c[5] if len(c) > 5 else c[0] for c in self.fake.calls]
        load = next(i for i, c in enumerate(self.fake.calls) if c[4:] == ["--exec", *bas.NFT_LOAD])
        agent = next(i for i, c in enumerate(self.fake.calls) if c[:4] == ["-d", bas.DISTRO, "-u", bas.USER])
        self.assertLess(load, agent, names)
        self.assertIn("127.0.0.0/8 accept", self.fake.loaded[-1])  # NAT : boucle locale permise

    def test_mode_mirrored_sans_boucle_locale(self):
        self.fake.mode = "mirrored"  # 127.0.0.1 mènerait aux services de Windows
        bas.run("ls")
        self.assertNotIn("127.0.0.0/8 accept", self.fake.loaded[-1])
        self.fake.mode = "inconnu"
        bas.run("ls")
        self.assertNotIn("127.0.0.0/8 accept", self.fake.loaded[-1])

    def test_internet_public_selon_le_reglage(self):
        bas.run("ls")
        self.assertNotIn(f"meta skuid {bas.AGENT_UID} accept", self.fake.loaded[-1])
        bas.config.update({"bac_a_sable": {"reseau_public": True}})
        state = bas.lock_network()
        self.assertTrue(state["internet_public"])
        self.assertIn("10.255.255.254 udp dport 53 accept", self.fake.loaded[-1])  # le résolveur de resolv.conf
        self.assertIn(f"meta skuid {bas.AGENT_UID} accept", self.fake.loaded[-1])

    def test_verrou_rate_commande_refusee(self):
        self.fake.nft_code = 1
        with self.assertRaises(bas.BacASableError):
            bas.run("ls")
        self.fake.nft_code, self.fake.keep_listing = 0, False  # chargée mais absente à la relecture
        with self.assertRaises(bas.BacASableError):
            bas.run("ls")
        self.assertFalse(any(c[:4] == ["-d", bas.DISTRO, "-u", bas.USER] for c in self.fake.calls))

    def test_root_sans_verrou(self):
        bas.run("id", user="root")
        self.assertEqual(self.fake.loaded, [])


class _Image(http.server.BaseHTTPRequestHandler):
    body, hits = b"", []

    def do_GET(self):
        _Image.hits.append(self.path)
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *a):
        pass


class Telechargement(unittest.TestCase):
    def setUp(self):
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Image)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(shutil.rmtree, bas.BASE, ignore_errors=True)
        _Image.body, _Image.hits = b"contenu de l'image", []
        self.original = dict(bas.IMAGES)
        self.addCleanup(lambda: (bas.IMAGES.clear(), bas.IMAGES.update(self.original)))

    def pin(self, sha):
        bas.IMAGES["essai"] = {"nom": "Essai", "url": f"http://127.0.0.1:{self.server.server_address[1]}/image.tar",
                               "sha256": sha, "fichier": "essai.tar"}

    def test_empreinte_differente_refusee(self):
        self.pin("0" * 64)
        with self.assertRaises(bas.BacASableError) as ctx:
            bas.download("essai")
        self.assertIn("SHA-256", str(ctx.exception))
        self.assertEqual(list(bas.IMAGES_DIR.iterdir()), [])  # ni fichier, ni morceau

    def test_bonne_empreinte_gardee_puis_reutilisee(self):
        import hashlib
        self.pin(hashlib.sha256(_Image.body).hexdigest())
        path = bas.download("essai")
        self.assertEqual(path.read_bytes(), _Image.body)
        bas.download("essai")
        self.assertEqual(len(_Image.hits), 1)  # déjà là et intacte : pas de second téléchargement
        path.write_bytes(b"abime")
        bas.download("essai")
        self.assertEqual(len(_Image.hits), 2)  # abîmée : retéléchargée
        self.assertEqual(path.read_bytes(), _Image.body)


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

    def test_creer_executer_supprimer(self):
        bas.create("alpine")
        code, out = bas.run("id -un; ls /mnt; echo fin")
        self.assertEqual(code, 0, out)
        lines = out.splitlines()
        self.assertEqual(lines[0], bas.USER)
        self.assertEqual(lines[-1], "fin")
        self.assertNotIn("c", lines[1:-1])  # pas de C: monté dans /mnt
        # La commande arrive telle quelle à sh : $? et les guillemets ne sont pas développés en route
        self.assertEqual(bas.run("false; echo \"code=$?\""), (0, "code=1"))
        self.assertEqual(bas.run("exit 3")[0], 3)
        self.assertEqual(bas.run("which cmd.exe || echo absent")[1], "absent")  # pas de lancement d'exe Windows
        self.assertEqual(bas.run("id -u")[1], str(bas.AGENT_UID))
        # Réseau : coupé pour l'agent (Internet et DNS), gardé pour root
        web = "wget -q -T 5 -O /dev/null https://dl-cdn.alpinelinux.org/ >/dev/null 2>&1 && echo ouvert || echo ferme"
        self.assertEqual(bas.run(web)[1], "ferme")
        self.assertEqual(bas.run("nslookup dl-cdn.alpinelinux.org >/dev/null 2>&1 && echo ouvert || echo ferme")[1],
                         "ferme")
        self.assertEqual(bas.run(web, user="root")[1], "ouvert")
        self.assertIn("Operation not permitted", bas.run("nft flush ruleset 2>&1")[1])
        # Internet public permis : le site passe, toujours rien vers la passerelle de Windows
        state = bas.lock_network(public=True)
        self.assertTrue(state["internet_public"])
        code, out = bas.run_wsl(["-d", bas.DISTRO, "-u", bas.USER, "--exec", "sh", "-c", web])
        self.assertEqual(out, "ouvert")
        gateway = bas.run("ip route | awk '/default/ {print $3; exit}'", user="root")[1]
        code, out = bas.run_wsl(["-d", bas.DISTRO, "-u", bas.USER, "--exec", "sh", "-c",
                                 f"nc -w 3 {gateway} 53 </dev/null 2>&1; echo code=$?"])
        self.assertNotIn("code=0", out)
        bas.delete()
        self.assertFalse(bas.exists())


if __name__ == "__main__":
    unittest.main()
