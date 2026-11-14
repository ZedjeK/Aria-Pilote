"""Bac à sable Linux : une distribution WSL 2 à part, créée par Aria Pilote, où l'agent pourra travailler sans toucher
au vrai PC.

- WSL lui-même s'active une fois, par l'utilisateur, en administrateur : Aria le détecte seulement.
- L'image de base (Debian ou Alpine) est téléchargée à une version épinglée et refusée si son empreinte SHA-256 ne
  correspond pas ; elle est gardée dans bac-a-sable\\images pour une recréation sans réseau.
- /etc/wsl.conf est écrit dans l'archive avant l'import : la distribution naît cloisonnée, sans que son premier
  démarrage ait jamais monté C: (pas de disques Windows dans /mnt, pas de lancement d'exe Windows, pas de PATH
  Windows).
- L'agent y sera l'utilisateur « agent », sans sudo ni mot de passe. Root ne sert qu'à la préparation.
- La distribution s'inscrit dans le registre WSL du compte Windows (écriture hors du dossier de données, déclarée
  dans la fiche) ; son disque est dans bac-a-sable\\disque. supprimer() la désinscrit et efface ce disque.
"""

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import time
from io import BytesIO

import httpx

from . import VERSION, config

DISTRO = "aria-bac-a-sable"
BASE = config.ROOT / "bac-a-sable"
IMAGES_DIR = BASE / "images"
DISK_DIR = BASE / "disque"
STATE_FILE = BASE / "image.json"
USER = "agent"
CHUNK = 1 << 20
WSL_TIMEOUT_S = 300  # un import peut être long sur un disque lent

# Images de base, épinglées. Debian : l'image officielle construite par Debian, telle que la référence le catalogue de
# WSL (github.com/microsoft/WSL, distributions/DistributionInfo.json). Alpine : la minirootfs du site officiel et son
# fichier .sha256. Relevé le 14/11/2026.
IMAGES = {
    "debian": {
        "nom": "Debian (image WSL officielle)",
        "url": "https://salsa.debian.org/debian/WSL/-/jobs/9606244/artifacts/raw/Debian_WSL_AMD64_v1.26.0.0.wsl",
        "sha256": "5ec7dc68216e75d1d4d4761474e99d8461a98d316537110314b137122a879e0f",
        "fichier": "Debian_WSL_AMD64_v1.26.0.0.wsl",
    },
    "alpine": {
        "nom": "Alpine 3.24.4 (minirootfs)",
        "url": "https://dl-cdn.alpinelinux.org/alpine/v3.24/releases/x86_64/alpine-minirootfs-3.24.4-x86_64.tar.gz",
        "sha256": "cdaa38cb8de3537967ee5b6c9b6cd00cc962da2b018e6eea48696bb274923012",
        "fichier": "alpine-minirootfs-3.24.4-x86_64.tar.gz",
    },
}
DEFAULT_IMAGE = "debian"

# Écrit dans l'archive avant l'import : appliqué dès le premier démarrage
WSL_CONF = """# Écrit par Aria Pilote : bac à sable cloisonné
[automount]
enabled = false
mountFsTab = false

[interop]
enabled = false
appendWindowsPath = false
"""
# Ajouté une fois l'utilisateur créé (un utilisateur par défaut qui n'existe pas encore ferait échouer le démarrage)
WSL_CONF_USER = f"\n[user]\ndefault = {USER}\n"

# Préparation, en root, au premier démarrage : l'utilisateur de l'agent, sans mot de passe utilisable ni sudo
SETUP = f"""set -e
if command -v useradd >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash {USER}
else
    adduser -D -s /bin/sh {USER}
fi
passwd -l {USER} >/dev/null 2>&1 || true
for g in sudo wheel admin; do
    if getent group "$g" >/dev/null 2>&1; then gpasswd -d {USER} "$g" >/dev/null 2>&1 || true; fi
done
printf '%s' '{WSL_CONF_USER}' >> /etc/wsl.conf
"""


class BacASableError(Exception):
    pass


# --- appels à wsl.exe --------------------------------------------------------------------------------------------

def run_wsl(args, stdin=None, timeout=WSL_TIMEOUT_S):
    """Lance wsl.exe ; renvoie (code, sortie). WSL_UTF8=1 pour ce seul appel : sans lui, wsl.exe écrit en UTF-16.
    Remplacé par un faux dans les tests."""
    env = {**os.environ, "WSL_UTF8": "1"}
    try:
        proc = subprocess.run(["wsl.exe", *args], input=stdin, capture_output=True, env=env, timeout=timeout,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except FileNotFoundError:
        return None, "wsl.exe introuvable"
    except subprocess.TimeoutExpired:
        return None, f"wsl.exe n'a pas répondu en {timeout} s"
    output = (proc.stdout + proc.stderr).decode("utf-8", errors="replace").replace("\x00", "")
    return proc.returncode, output.strip()


def _wsl_ok(args, what, **kw):
    code, out = run_wsl(args, **kw)
    if code != 0:
        raise BacASableError(f"{what} : {out or 'échec sans message'} (code {code}).")
    return out


def wsl_installed():
    code, _ = run_wsl(["--status"], timeout=30)
    return code == 0


def distributions():
    code, out = run_wsl(["--list", "--quiet"], timeout=30)
    if code != 0:  # aucune distribution : wsl.exe répond par un message et un code d'erreur
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def exists():
    return DISTRO in distributions()


def status():
    """État pour l'interface et l'agent ; ne lance rien d'autre que wsl.exe en lecture."""
    installed = wsl_installed()
    created = installed and exists()
    try:
        image = json.loads(STATE_FILE.read_text(encoding="utf-8")) if created and STATE_FILE.is_file() else None
    except (OSError, ValueError):
        image = None
    return {"wsl": installed, "cree": created, "distribution": DISTRO, "image": image,
            "images": {k: v["nom"] for k, v in IMAGES.items()}, "image_par_defaut": DEFAULT_IMAGE}


# --- image de base -----------------------------------------------------------------------------------------------

def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8 * CHUNK):
            h.update(chunk)
    return h.hexdigest()


def download(key, progress=None):
    """L'image `key`, téléchargée si besoin et vérifiée ; une image déjà là mais abîmée est retéléchargée."""
    spec = IMAGES[key]
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    target = IMAGES_DIR / spec["fichier"]
    if target.is_file() and _sha256(target) == spec["sha256"]:
        return target
    part = target.with_name(target.name + ".part")
    h, done = hashlib.sha256(), 0
    try:
        with httpx.stream("GET", spec["url"], headers={"User-Agent": f"AriaPilote/{VERSION}"},
                          timeout=httpx.Timeout(30, read=120), follow_redirects=True) as resp:
            if resp.status_code != 200:
                raise BacASableError(f"Image {spec['nom']} : le serveur a répondu {resp.status_code}.")
            with open(part, "wb") as out:
                for chunk in resp.iter_bytes(CHUNK):
                    out.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done)
    except httpx.HTTPError as exc:
        part.unlink(missing_ok=True)
        raise BacASableError(f"Image {spec['nom']} : téléchargement impossible ({exc}).")
    if h.hexdigest() != spec["sha256"]:
        part.unlink(missing_ok=True)
        raise BacASableError(f"Image {spec['nom']} : empreinte SHA-256 différente de celle épinglée, fichier refusé.")
    part.replace(target)
    return target


def prepare_archive(source, dest):
    """Copie de l'archive de l'image (tar, compressée ou non), avec notre /etc/wsl.conf à la place du sien."""
    conf = WSL_CONF.encode("utf-8")
    replaced = False
    with tarfile.open(source, mode="r:*") as src, tarfile.open(dest, mode="w") as out:
        for member in src:
            name = member.name.removeprefix("./")
            if name == "etc/wsl.conf":
                replaced = True
                continue  # remplacé ci-dessous
            out.addfile(member, src.extractfile(member) if member.isreg() else None)
        info = tarfile.TarInfo("etc/wsl.conf")
        info.size, info.mode, info.mtime = len(conf), 0o644, int(time.time())
        out.addfile(info, BytesIO(conf))
    return replaced


# --- création et suppression -------------------------------------------------------------------------------------

def create(key=DEFAULT_IMAGE, progress=None):
    """Crée le bac à sable avec l'image `key`. En cas d'échec après l'import, la distribution à moitié faite est
    désinscrite : on ne laisse jamais un bac à sable sans cloisonnement complet."""
    if key not in IMAGES:
        raise BacASableError(f"Image inconnue : {key}. Choix : {', '.join(IMAGES)}.")
    if not wsl_installed():
        raise BacASableError("WSL n'est pas installé. Installe-le toi-même, une fois, dans un terminal administrateur "
                             "(wsl --install --no-distribution), puis redémarre le PC.")
    if exists():
        raise BacASableError("Le bac à sable existe déjà.")
    image = download(key, progress)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    prepared = IMAGES_DIR / "prepare.tar"
    try:
        prepare_archive(image, prepared)
        if DISK_DIR.exists():
            shutil.rmtree(DISK_DIR)  # reste d'une tentative précédente : jamais un disque inscrit (vérifié plus haut)
        DISK_DIR.mkdir(parents=True)
        _wsl_ok(["--import", DISTRO, str(DISK_DIR), str(prepared), "--version", "2"], "Import de la distribution")
    finally:
        prepared.unlink(missing_ok=True)
    try:
        _wsl_ok(["-d", DISTRO, "-u", "root", "--exec", "sh", "-c", SETUP], "Préparation du bac à sable")
        _wsl_ok(["--terminate", DISTRO], "Redémarrage du bac à sable")
    except BacASableError:
        run_wsl(["--unregister", DISTRO])
        raise
    STATE_FILE.write_text(json.dumps({"image": key, "nom": IMAGES[key]["nom"], "sha256": IMAGES[key]["sha256"],
                                      "cree_le": time.strftime("%Y-%m-%d %H:%M")}, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    return status()


def delete():
    """Désinscrit la distribution et efface son disque : rien de l'utilisateur n'y vit (le projet n'y est qu'une
    copie). Les images téléchargées sont gardées."""
    if exists():
        _wsl_ok(["--unregister", DISTRO], "Suppression du bac à sable")
    if DISK_DIR.exists():
        shutil.rmtree(DISK_DIR, ignore_errors=True)
    STATE_FILE.unlink(missing_ok=True)
    return status()


def run(command, user=USER, timeout=120):
    """Une commande dans le bac à sable (en `agent` par défaut) ; renvoie (code, sortie). Servira à l'outil de l'agent
    et aux vérifications du cloisonnement."""
    if not exists():
        raise BacASableError("Le bac à sable n'existe pas : crée-le d'abord.")
    # --exec : la commande va telle quelle à sh. Avec « -- », wsl.exe la passerait d'abord au shell par défaut de
    # l'utilisateur, qui développerait $?, $HOME et les guillemets avant que sh ne la voie
    return run_wsl(["-d", DISTRO, "-u", user, "--cd", "~", "--exec", "sh", "-c", command], timeout=timeout)
