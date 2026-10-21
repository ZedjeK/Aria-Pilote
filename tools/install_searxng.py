"""Installe SearXNG en natif Windows dans services/searxng/ (sans Docker ni WSL).

- télécharge le code officiel à une version épinglée ;
- installe ses dépendances : dans un venv (développement) ou dans un dossier
  site-packages à part (--portable, utilisé par build_portable.py, sans chemin absolu) ;
- applique la retouche Windows (import Unix `pwd` rendu facultatif) ;
- écrit la configuration : 127.0.0.1:8888 uniquement, format JSON activé, clé secrète aléatoire.

Usage : python tools/install_searxng.py [--commit SHA] [--portable] [--service-dir DOSSIER]
"""

import argparse
import io
import json
import os
import secrets
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

REPO = "searxng/searxng"
COMMIT = "81371861f67226091d1c4df2e3908a11d3b54eb9"  # master du 19/10/2026

# Seule retouche nécessaire sous Windows : `pwd` n'existe pas, et il ne sert
# que dans un message d'erreur quand la base Valkey est activée (elle ne l'est pas).
PATCH_FILE = "searx/valkeydb.py"
PATCH_OLD = "import pwd\n"
PATCH_NEW = "try:\n    import pwd\nexcept ImportError:  # Windows (retouche Aria Pilote)\n    pwd = None\n"

SETTINGS_TEMPLATE = """# Configuration SearXNG d'Aria Pilote (générée par tools/install_searxng.py)
use_default_settings: true

general:
  instance_name: "Aria Pilote"

server:
  bind_address: "127.0.0.1"
  port: 8888
  secret_key: "{secret}"
  limiter: false
  public_instance: false
  image_proxy: false

search:
  formats:
    - html
    - json

valkey:
  url: false
"""


def download_source(src_dir, commit):
    url = f"https://codeload.github.com/{REPO}/zip/{commit}"
    print(f"[1/5] Téléchargement de SearXNG {commit[:7]}…")
    req = urllib.request.Request(url, headers={"User-Agent": "aria-pilote"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()
    print(f"      {len(data) / 1024**2:.1f} Mo")
    if src_dir.exists():
        shutil.rmtree(src_dir)
    src_dir.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        prefix = z.namelist()[0].split("/")[0] + "/"
        for member in z.infolist():
            rel = member.filename[len(prefix):]
            if not rel or member.is_dir():
                continue
            target = (src_dir / rel).resolve()
            if src_dir.resolve() not in target.parents:
                sys.exit(f"Chemin suspect dans l'archive : {member.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(member))


def patch_windows(src_dir):
    print("[2/5] Retouche Windows…")
    path = src_dir / PATCH_FILE
    text = path.read_text(encoding="utf-8")
    if "pwd = None" in text:
        return  # déjà retouché (le commentaire a pu changer avec le nom de l'appli)
    if text.count(PATCH_OLD) != 1:
        sys.exit(f"Retouche impossible : {PATCH_FILE} a changé dans cette version de SearXNG.")
    path.write_text(text.replace(PATCH_OLD, PATCH_NEW, 1), encoding="utf-8")


def freeze_version(src_dir, commit):
    # Sans dépôt git, SearXNG essaierait d'appeler git pour connaître sa version
    (src_dir / "searx" / "version_frozen.py").write_text(
        f'VERSION_STRING = "{date.today():%Y.%m.%d}+{commit[:7]}"\n'
        f'VERSION_TAG = "{date.today():%Y.%m.%d}+{commit[:7]}"\n'
        f'DOCKER_TAG = "{commit[:7]}"\n'
        f'GIT_URL = "https://github.com/{REPO}"\n'
        f'GIT_BRANCH = "master"\n',
        encoding="utf-8",
    )


def install_dependencies(service_dir, src_dir, python, portable):
    print("[3/5] Dépendances Python…")
    env = {**os.environ, "PYTHONNOUSERSITE": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_NO_WARN_SCRIPT_LOCATION": "1"}
    requirements = [str(src_dir / "requirements.txt")]
    if portable:
        # Dossier à part, branché par PYTHONPATH : rien d'absolu, le dossier reste déplaçable
        target = service_dir / "site-packages"
        if target.exists():
            shutil.rmtree(target)
        subprocess.run(
            [str(python), "-m", "pip", "install", "--quiet", "--target", str(target), "-r", *requirements, "tzdata"],
            check=True, env=env,
        )
        return
    venv = service_dir / ".venv"
    if not venv.exists():
        subprocess.run([str(python), "-m", "venv", str(venv)], check=True)
    venv_python = venv / "Scripts" / "python.exe"
    subprocess.run([str(venv_python), "-m", "pip", "install", "--quiet", "--upgrade", "pip"], check=True, env=env)
    subprocess.run([str(venv_python), "-m", "pip", "install", "--quiet", "-r", *requirements, "tzdata"], check=True, env=env)


def write_settings(service_dir):
    print("[4/5] Configuration…")
    settings = service_dir / "settings.yml"
    if settings.exists():
        print("      settings.yml existant conservé")
        return
    settings.write_text(SETTINGS_TEMPLATE.format(secret=secrets.token_hex(32)), encoding="utf-8")


def install(service_dir, commit=COMMIT, python=sys.executable, portable=False, src_from=None):
    """Installe SearXNG ; `src_from` réutilise un code source déjà téléchargé et retouché."""
    service_dir = Path(service_dir)
    src_dir = service_dir / "src"
    if src_from:
        print(f"[1/5] Copie du code SearXNG depuis {src_from}…")
        if src_dir.exists():
            shutil.rmtree(src_dir)
        shutil.copytree(src_from, src_dir, ignore=shutil.ignore_patterns("__pycache__"))
    else:
        download_source(src_dir, commit)
    patch_windows(src_dir)
    freeze_version(src_dir, commit)
    install_dependencies(service_dir, src_dir, python, portable)
    write_settings(service_dir)
    manifest = {"repo": REPO, "commit": commit, "installed": date.today().isoformat(), "portable": portable}
    (service_dir / "service.json").write_text(json.dumps(manifest, indent=4), encoding="utf-8")
    print(f"[5/5] SearXNG installé dans {service_dir}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--commit", default=COMMIT)
    parser.add_argument("--portable", action="store_true")
    parser.add_argument("--service-dir", default=str(ROOT / "services" / "searxng"))
    args = parser.parse_args()
    install(args.service_dir, args.commit, portable=args.portable)


if __name__ == "__main__":
    main()
