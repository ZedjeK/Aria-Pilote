"""Télécharge les binaires Windows de llama-server dans engines/ (le même code que Réglages › Moteurs).

- llama.cpp officiel : dernière build bXXXXX de ggml-org/llama.cpp
- fork PrismML : dernière release prism-bXXXXX de PrismML-Eng/llama.cpp (lit PQ2_0 et PTQ1_0)

La version en place est gardée à côté (engines/<moteur>.prev), comme depuis l'appli.

Usage : python tools/install_engines.py [--backend cuda-12.4] [--only prism] [--prism-tag TAG]
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from aria_pilote import engines  # noqa: E402


def progress(done, total):
    print(f"\r  {done * 100 // total} %", end="", flush=True)


async def run(name, backend, tag):
    release = await (engines.release_by_tag(name, tag) if tag else engines.latest(name, backend))
    print(f"[{name}] {engines.ENGINES[name]['repo']} {release['tag_name']} ({backend})")
    await engines.install(name, release, backend, progress)
    print(f"\n  installé : {engines.find_server(name)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--backend", choices=engines.BACKENDS, default=engines.DEFAULT_BACKEND)
    parser.add_argument("--only", choices=list(engines.ENGINES))
    parser.add_argument("--prism-tag", help="release PrismML précise (défaut : la dernière)")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    for name in engines.ENGINES:
        if args.only in (None, name):
            try:
                asyncio.run(run(name, args.backend, args.prism_tag if name == "prism" else None))
            except (engines.UpdateError, RuntimeError) as exc:
                sys.exit(f"\n[{name}] {exc}")


if __name__ == "__main__":
    main()
