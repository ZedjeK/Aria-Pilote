"""Construit la version portable d'Aria Pilote à côté du code source : ..\\Aria-Pilote-X.Y.Z\\

Organisation attendue :
    Aria - Pilote\\
    ├── Source code\\        ce projet (on développe ici)
    └── Aria-Pilote-0.6.0\\   l'appli utilisée au quotidien

La version est construite dans un dossier temporaire, puis mise en place :
- même version déjà présente : mise à jour sur place ;
- nouvelle version : tes affaires sont déplacées depuis la version précédente, qui est ensuite supprimée.
Dans les deux cas, models\\, data\\ et config.json ne sont jamais écrasés.

Le dossier contient :
- python\\            CPython 3.11 portable (python-build-standalone), vérifié par SHA-256 ;
- aria_pilote\\, tools\\ le code de l'appli ;
- engines\\           les moteurs llama.cpp, en liens physiques vers ceux du code source (sauf --no-engines) ;
- services\\searxng\\  SearXNG et ses dépendances dans un site-packages à part.

Usage : python tools/build_portable.py [--no-engines] [--releases-dir DOSSIER]
"""

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".build_cache"

PYTHON_RELEASE = "20261013"
PYTHON_ASSET = f"cpython-3.11.17+{PYTHON_RELEASE}-x86_64-pc-windows-msvc-install_only.tar.gz"
PYTHON_URL = f"https://github.com/astral-sh/python-build-standalone/releases/download/{PYTHON_RELEASE}"

CSC = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"

# Ce qui appartient à l'utilisateur dans une version installée : jamais écrasé
USER_ITEMS = ("models", "data", "config.json")
EXE_NAME = "Aria-Pilote.exe"
RELEASE_RE = re.compile(r"^Aria-Pilote-(\d+)\.(\d+)\.(\d+)$")
# Ancien nom de l'appli : ses dossiers ne servent plus que de source pour reprendre tes affaires
LEGACY_RELEASE_RE = re.compile(r"^Aria-Home_v(\d+)\.(\d+)\.(\d+)$")
LEGACY_EXE_NAMES = ("Aria Home.exe",)

sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
import install_searxng  # noqa: E402
import make_icon  # noqa: E402
from aria_pilote import VERSION  # noqa: E402


def step(text):
    print(f"\n=== {text}")


def fetch(url, target):
    if target.exists():
        print(f"  en cache : {target.name}")
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "aria-pilote"})
    tmp = target.with_suffix(target.suffix + ".part")
    with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as out:
        shutil.copyfileobj(resp, out)
    tmp.replace(target)
    print(f"  téléchargé : {target.name} ({target.stat().st_size / 1024**2:.1f} Mo)")
    return target


def link_or_copy(src, dst):
    """Lien physique NTFS (aucun octet dupliqué), ou copie si impossible (autre disque)."""
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


# --- étapes de construction ------------------------------------------------------

def install_python(dest):
    step("Python portable")
    archive = fetch(f"{PYTHON_URL}/{PYTHON_ASSET}", CACHE / PYTHON_ASSET)
    sums = fetch(f"{PYTHON_URL}/SHA256SUMS", CACHE / f"SHA256SUMS-{PYTHON_RELEASE}")
    expected = next((line.split()[0] for line in sums.read_text().splitlines() if line.endswith(PYTHON_ASSET)), None)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != expected:
        archive.unlink()
        sys.exit(f"SHA-256 du Python portable invalide ({digest} au lieu de {expected}) : archive supprimée.")
    print("  SHA-256 vérifié")
    with tarfile.open(archive) as tar:
        tar.extractall(dest, filter="data")  # l'archive contient un dossier « python/ »
    python = dest / "python" / "python.exe"
    env = {**os.environ, "PYTHONNOUSERSITE": "1"}
    if subprocess.run([str(python), "-m", "pip", "--version"], capture_output=True, env=env).returncode != 0:
        subprocess.run([str(python), "-m", "ensurepip"], check=True, env=env)
    return python


def copy_app(dest):
    step("Code de l'appli")
    shutil.copytree(ROOT / "aria_pilote", dest / "aria_pilote", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(ROOT / "tools", dest / "tools", ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "launcher"))
    shutil.copy2(ROOT / "requirements.txt", dest / "requirements.txt")


def install_dependencies(python, dest):
    step("Dépendances d'Aria Pilote")
    env = {**os.environ, "PYTHONNOUSERSITE": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
    subprocess.run(
        [str(python), "-m", "pip", "install", "--quiet", "--no-warn-script-location", "-r", str(dest / "requirements.txt")],
        check=True, env=env,
    )


def copy_engines(dest):
    step("Moteurs llama.cpp")
    for name in ("llama.cpp", "prism"):
        src = ROOT / "engines" / name
        if not src.is_dir():
            print(f"  absent dans le code source : {name} (à installer avec tools/install_engines.py)")
            continue
        shutil.copytree(src, dest / "engines" / name, copy_function=link_or_copy)
        print(f"  lié : {name}")


def install_search(python, dest):
    step("SearXNG")
    dev_src = ROOT / "services" / "searxng" / "src"
    install_searxng.install(
        dest / "services" / "searxng",
        python=python,
        portable=True,
        src_from=dev_src if dev_src.is_dir() else None,
    )


def build_launcher(dest):
    step("Lanceur Aria-Pilote.exe")
    if not CSC.exists():
        sys.exit(f"Compilateur C# introuvable : {CSC}")
    icon_dir = CACHE / "icon"
    icon_dir.mkdir(parents=True, exist_ok=True)
    (icon_dir / "aria.ico").write_bytes(make_icon.ico())
    subprocess.run([
        str(CSC), "/nologo", "/target:winexe", "/optimize+",
        f"/out:{dest / EXE_NAME}",
        f"/win32icon:{icon_dir / 'aria.ico'}",
        "/reference:System.Windows.Forms.dll",
        str(ROOT / "tools" / "launcher" / "Launcher.cs"),
    ], check=True)


# --- mise en place -----------------------------------------------------------------

def find_releases(releases_dir, pattern=RELEASE_RE):
    found = []
    for p in releases_dir.iterdir():
        m = pattern.match(p.name)
        if p.is_dir() and m:
            found.append((tuple(int(x) for x in m.groups()), p))
    return [p for _, p in sorted(found)]


def looks_like_release(folder):
    return any((folder / name).exists() for name in (EXE_NAME, *LEGACY_EXE_NAMES))


def move_user_items(src, dest):
    for name in USER_ITEMS:
        if (src / name).exists():
            target = dest / name
            if target.is_dir() and not any(target.iterdir()):
                target.rmdir()  # dossier vide créé par la construction
            if target.exists():
                sys.exit(f"Conflit : {target} existe déjà, rien n'a été déplacé.")
            shutil.move(str(src / name), str(target))
            print(f"  déplacé : {name}")


def install_release(staging, releases_dir):
    """Met la version construite en place sans jamais perdre models, data ni config.json."""
    step("Mise en place")
    target = releases_dir / f"Aria-Pilote-{VERSION}"
    # Priorité : la même version, sinon la plus récente, sinon un dossier de l'ancien nom (Aria Home)
    previous = [p for p in find_releases(releases_dir) if p != target] or find_releases(releases_dir, LEGACY_RELEASE_RE)
    source = target if target.exists() else (previous[-1] if previous else None)
    if source:
        # Un dossier dont un fichier est ouvert (appli en marche) ne peut pas être renommé :
        # on le vérifie avant de toucher à quoi que ce soit
        probe = source.with_name(source.name + ".verif")
        try:
            source.rename(probe)
            probe.rename(source)
        except OSError:
            sys.exit(f"Arrêt : {source.name} est en cours d'utilisation. Quitte Aria Pilote puis relance la construction.")
        print(f"  reprise de tes affaires depuis {source.name}")
        move_user_items(source, staging)
        leftovers = [n for n in USER_ITEMS if (source / n).exists()]
        if leftovers:
            sys.exit(f"Arrêt : {', '.join(leftovers)} encore présent(s) dans {source}, rien n'a été supprimé.")
        if not looks_like_release(source):
            sys.exit(f"Arrêt : {source} ne ressemble pas à une version d'Aria Pilote, il n'est pas supprimé.")
        shutil.rmtree(source)
        if source == target:
            print(f"  ancien contenu de {source.name} remplacé")
        else:
            print(f"  ancienne version supprimée : {source.name}")
    shutil.move(str(staging), str(target))
    return target


def point_dev_config(release):
    """La version de développement utilise les modèles de la version installée (pas de doublon)."""
    from aria_pilote import config

    rel = os.path.relpath(release / "models", ROOT).replace("\\", "/")
    config.update({"model_dirs": [rel]})
    print(f"  config de dev : modèles lus dans {rel}")


def folder_size(path):
    seen, total = set(), 0
    for f in Path(path).rglob("*"):
        if f.is_file():
            st = f.stat()
            if (st.st_dev, st.st_ino) not in seen:
                seen.add((st.st_dev, st.st_ino))
                total += st.st_size
    return total


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--releases-dir", default=str(ROOT.parent), help="où placer Aria-Pilote-X.Y.Z (par défaut : à côté du code source)")
    parser.add_argument("--no-engines", action="store_true", help="ne pas inclure les moteurs (~2,4 Go)")
    args = parser.parse_args()

    releases_dir = Path(args.releases_dir).resolve()
    staging = CACHE / "staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    print(f"Construction d'Aria Pilote v{VERSION}")

    python = install_python(staging)
    copy_app(staging)
    install_dependencies(python, staging)
    if not args.no_engines:
        copy_engines(staging)
    install_search(python, staging)
    build_launcher(staging)
    for folder in ("data", "models"):
        (staging / folder).mkdir(exist_ok=True)

    release = install_release(staging, releases_dir)
    if releases_dir == ROOT.parent.resolve():
        point_dev_config(release)

    step("Terminé")
    for part in ("python", "aria_pilote", "engines", "services", "models"):
        if (release / part).exists():
            print(f"  {part:<10} {folder_size(release / part) / 1024**2:8.0f} Mo")
    print(f"\nVersion installée : {release}\nLancement : double-clic sur « Aria-Pilote.exe »")


if __name__ == "__main__":
    main()
