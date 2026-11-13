"""Télécharge le Chromium du navigateur piloté dans chromium/ (version de dev), ou dans le dossier donné (construction).

C'est le Chromium qui va avec la version de Playwright installée (requirements.txt) : Playwright le télécharge
depuis son propre serveur et le range par numéro de build (chromium-NNNN). Seul Chromium complet est pris, sans sa
variante sans fenêtre : l'utilisateur voit ce que fait l'agent.

Usage : python tools/install_navigateur.py [--dest DOSSIER]
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def install(dest, python=sys.executable):
    """Installe Chromium dans `dest` avec le Playwright de `python` ; lève CalledProcessError en cas d'échec."""
    dest = Path(dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(dest)}
    subprocess.run([str(python), "-m", "playwright", "install", "chromium", "--no-shell"], check=True, env=env)
    return dest


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dest", default=str(ROOT / "chromium"), help="dossier de destination (défaut : chromium/)")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        dest = install(args.dest)
    except subprocess.CalledProcessError as exc:
        sys.exit(f"Installation de Chromium impossible (code {exc.returncode}).")
    found = sorted(dest.glob("chromium-*/chrome-win64/chrome.exe"))
    print(f"Chromium installé : {found[-1] if found else 'introuvable après installation'}")


if __name__ == "__main__":
    main()
