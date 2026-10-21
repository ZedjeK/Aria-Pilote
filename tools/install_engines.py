"""Télécharge les binaires Windows de llama-server dans engines/.

- llama.cpp officiel : dernière build bXXXXX de ggml-org/llama.cpp
- fork PrismML : version épinglée par PrismML-Eng/Bonsai-demo (lit PQ2_0 et PTQ1_0)

Usage : python tools/install_engines.py [--backend cuda-12.4] [--only prism] [--prism-tag TAG]
"""

import argparse
import json
import re
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINES_DIR = ROOT / "engines"
DOWNLOADS_DIR = ENGINES_DIR / "_downloads"

UPSTREAM_REPO = "ggml-org/llama.cpp"
PRISM_REPO = "PrismML-Eng/llama.cpp"
PRISM_TAG = "prism-b10777-e3de715"

# backend -> (motif du zip llama, motif du zip runtime CUDA ou None)
BACKENDS = {
    "cuda-12.4": (r"^llama-.+-bin-win-cuda-12\.4-x64\.zip$", r"^cudart-llama-bin-win-cuda-12\.4-x64\.zip$"),
    "cuda-13": (r"^llama-.+-bin-win-cuda-13\.\d+-x64\.zip$", r"^cudart-llama-bin-win-cuda-13\.\d+-x64\.zip$"),
    "vulkan": (r"^llama-.+-bin-win-vulkan-x64\.zip$", None),
    "cpu": (r"^llama-.+-bin-win-cpu-x64\.zip$", None),
}


def github(url):
    req = urllib.request.Request(url, headers={"User-Agent": "aria-pilote", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def find_asset(release, pattern):
    for asset in release["assets"]:
        if re.match(pattern, asset["name"]):
            return asset
    return None


def latest_upstream(pattern):
    # Les builds bXXXXX sont publiées en « prerelease » et /releases/latest pointe ailleurs :
    # on filtre nous-mêmes sur le format du tag
    for release in github(f"https://api.github.com/repos/{UPSTREAM_REPO}/releases?per_page=10"):
        if release["draft"] or not re.match(r"^b\d+$", release["tag_name"]):
            continue
        if find_asset(release, pattern):
            return release
    sys.exit("Aucune release officielle récente ne contient ce backend.")


def download(asset):
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    target = DOWNLOADS_DIR / asset["name"]
    if target.exists() and target.stat().st_size == asset["size"]:
        print(f"  déjà téléchargé : {asset['name']}")
        return target
    req = urllib.request.Request(asset["browser_download_url"], headers={"User-Agent": "aria-pilote"})
    tmp = target.with_suffix(".part")
    with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as out:
        done = 0
        while chunk := resp.read(1024 * 1024):
            out.write(chunk)
            done += len(chunk)
            print(f"\r  {asset['name']} : {done * 100 // asset['size']} %", end="", flush=True)
    print()
    tmp.replace(target)
    return target


def install(name, repo, release, backend):
    llama_pattern, cudart_pattern = BACKENDS[backend]
    print(f"[{name}] {repo} {release['tag_name']} ({backend})")
    llama_zip = download(find_asset(release, llama_pattern))
    dest = ENGINES_DIR / name
    if dest.exists():
        shutil.rmtree(dest)
    with zipfile.ZipFile(llama_zip) as z:
        z.extractall(dest)
    exe = next(dest.rglob("llama-server.exe"), None)
    if exe is None:
        sys.exit(f"llama-server.exe introuvable dans {llama_zip.name}")
    if cudart_pattern:
        cudart = find_asset(release, cudart_pattern)
        if cudart is None:
            sys.exit(f"Runtime CUDA introuvable dans la release {release['tag_name']}")
        with zipfile.ZipFile(download(cudart)) as z:
            # Les DLL CUDA doivent être à côté de llama-server.exe
            z.extractall(exe.parent)
    manifest = {"repo": repo, "tag": release["tag_name"], "backend": backend}
    (dest / "engine.json").write_text(json.dumps(manifest, indent=4), encoding="utf-8")
    print(f"  installé : {exe}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--backend", choices=BACKENDS, default="cuda-12.4")
    parser.add_argument("--only", choices=["llama.cpp", "prism"])
    parser.add_argument("--prism-tag", default=PRISM_TAG)
    args = parser.parse_args()

    if args.only in (None, "llama.cpp"):
        release = latest_upstream(BACKENDS[args.backend][0])
        install("llama.cpp", UPSTREAM_REPO, release, args.backend)
    if args.only in (None, "prism"):
        release = github(f"https://api.github.com/repos/{PRISM_REPO}/releases/tags/{args.prism_tag}")
        install("prism", PRISM_REPO, release, args.backend)


if __name__ == "__main__":
    main()
