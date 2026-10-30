"""Moteurs d'inférence : llama.cpp officiel et fork PrismML (requis pour Bonsai PQ2_0 / PTQ1_0).

Mise à jour (Réglages › Moteurs) : la release GitHub la plus récente du moteur pour le même backend (cuda-12.4…).
Les zip sont vérifiés (SHA-256 publié par GitHub), extraits à côté, puis échangés d'un coup avec la version en
place, gardée pour revenir en arrière. GitHub n'est interrogé qu'à la demande, sans aucune donnée envoyée.
"""

import asyncio
import hashlib
import json
import re
import shutil
import zipfile

import httpx

from . import VERSION, config

ENGINES = {
    "llama.cpp": {"label": "llama.cpp officiel", "dir": "llama.cpp", "repo": "ggml-org/llama.cpp",
                  "tag": r"^b\d+$"},
    "prism": {"label": "llama.cpp PrismML (Bonsai)", "dir": "prism", "repo": "PrismML-Eng/llama.cpp",
              "tag": r"^prism-b\d+-"},
}

# Formats que seul le fork PrismML sait lire (l'officiel les rejette comme types inconnus)
PRISM_QUANTS = {"PQ2_0", "PTQ1_0"}

DEFAULT_BACKEND = "cuda-12.4"
# backend -> (motif du zip llama, motif du zip runtime CUDA ou None)
BACKENDS = {
    "cuda-12.4": (r"^llama-.+-bin-win-cuda-12\.4-x64\.zip$", r"^cudart-llama-bin-win-cuda-12\.4-x64\.zip$"),
    "cuda-13": (r"^llama-.+-bin-win-cuda-13\.\d+-x64\.zip$", r"^cudart-llama-bin-win-cuda-13\.\d+-x64\.zip$"),
    "vulkan": (r"^llama-.+-bin-win-vulkan-x64\.zip$", None),
    "cpu": (r"^llama-.+-bin-win-cpu-x64\.zip$", None),
}
BUILD_RE = re.compile(r"b(\d+)")
CHUNK = 1024 * 1024

_latest = {}  # moteur -> {"tag", "date", "error", "release"} : la dernière vérification
_job = None   # la mise à jour en cours ou la dernière : {"name", "tag", "status", "done_bytes", "total_bytes", "error"}


class UpdateError(Exception):
    pass


def auto_engine(quant):
    return "prism" if quant in PRISM_QUANTS else "llama.cpp"


def engines_root():
    return config.path(config.get()["engines_dir"])


def engine_dir(name):
    return engines_root() / ENGINES[name]["dir"]


def _side(name, suffix):
    """Dossier voisin du moteur : .new (en cours d'installation), .prev (version précédente), .old (échange)."""
    return engines_root() / (ENGINES[name]["dir"] + suffix)


def find_server(name):
    base = engine_dir(name)
    if not base.is_dir():
        return None
    return next(base.rglob("llama-server.exe"), None)


def _manifest(folder):
    path = folder / "engine.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, ValueError):
        return None


def backend(name):
    found = (_manifest(engine_dir(name)) or {}).get("backend")
    return found if found in BACKENDS else DEFAULT_BACKEND


def _build(tag):
    match = BUILD_RE.search(tag or "")
    return int(match.group(1)) if match else None


def newer(latest, installed):
    """La release trouvée est plus récente que celle installée (numéro de build, sinon tag différent)."""
    if not latest:
        return False
    if not installed:
        return True
    a, b = _build(latest), _build(installed)
    return a > b if a is not None and b is not None else latest != installed


def info(name):
    exe = find_server(name)
    release = _manifest(engine_dir(name))
    previous = _manifest(_side(name, ".prev")) if find_server_in(_side(name, ".prev")) else None
    latest = _latest.get(name) or {}
    return {
        "name": name,
        "label": ENGINES[name]["label"],
        "installed": exe is not None,
        "path": str(exe) if exe else None,
        "release": release,
        "previous": (previous or {}).get("tag"),
        "latest": latest.get("tag"),
        "latest_error": latest.get("error"),
        "newer": newer(latest.get("tag"), (release or {}).get("tag")) if exe else bool(latest.get("tag")),
        "update": dict(_job) if _job and _job["name"] == name else None,
    }


def find_server_in(folder):
    return folder.is_dir() and next(folder.rglob("llama-server.exe"), None) is not None


def all_info():
    return [info(name) for name in ENGINES]


# --- Recherche de la dernière version -------------------------------------------------------------------------


async def _github(path):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": f"AriaPilote/{VERSION}"}
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        resp = await client.get(f"https://api.github.com/{path}", headers=headers)
    if resp.status_code == 403:
        raise RuntimeError("GitHub limite les requêtes pour l'instant : réessaie plus tard.")
    if resp.status_code != 200:
        raise RuntimeError(f"GitHub a répondu {resp.status_code}.")
    return resp.json()


def find_asset(release, pattern):
    return next((a for a in release.get("assets", []) if re.match(pattern, a["name"])), None)


def pick_release(name, releases, backend_name):
    """La release la plus récente du moteur qui a le zip de ce backend (et son runtime CUDA s'il en faut un).
    Les builds officielles sortent en « prerelease » plusieurs fois par jour : on filtre sur le format du tag."""
    llama, cudart = BACKENDS[backend_name]
    for release in releases:
        if release.get("draft") or not re.match(ENGINES[name]["tag"], release.get("tag_name", "")):
            continue
        if find_asset(release, llama) and (cudart is None or find_asset(release, cudart)):
            return release
    return None


async def latest(name, backend_name):
    releases = await _github(f"repos/{ENGINES[name]['repo']}/releases?per_page=20")
    release = pick_release(name, releases, backend_name)
    if release is None:
        raise RuntimeError(f"Aucune release récente pour {backend_name}.")
    return release


async def release_by_tag(name, tag):
    return await _github(f"repos/{ENGINES[name]['repo']}/releases/tags/{tag}")


async def check():
    """Cherche la dernière version de chaque moteur (une requête GitHub par moteur)."""
    for name in ENGINES:
        try:
            release = await latest(name, backend(name))
            _latest[name] = {"tag": release["tag_name"], "date": release.get("published_at"), "error": None,
                             "release": release}
        except (httpx.HTTPError, RuntimeError, ValueError, KeyError) as exc:
            _latest[name] = {"tag": None, "error": str(exc) if isinstance(exc, RuntimeError) else
                             "Impossible de joindre GitHub."}
    return all_info()


# --- Installation ----------------------------------------------------------------------------------------------


def _digest(asset):
    """SHA-256 publié par GitHub pour ce fichier (absent des anciennes releases)."""
    value = asset.get("digest") or ""
    return value[7:].lower() if value.startswith("sha256:") else None


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8 * CHUNK):
            h.update(chunk)
    return h.hexdigest()


async def _download(asset, progress):
    folder = engines_root() / "_downloads"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / asset["name"]
    digest = _digest(asset)
    # Le runtime CUDA garde le même nom d'une release à l'autre : déjà là et identique, on le réutilise
    if target.exists() and target.stat().st_size == asset["size"]:
        if digest is None or await asyncio.to_thread(_sha256, target) == digest:
            progress(asset["size"])
            return target
    part = target.with_name(target.name + ".part")
    h, done = hashlib.sha256(), 0
    headers = {"User-Agent": f"AriaPilote/{VERSION}"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=120), follow_redirects=True) as client:
        async with client.stream("GET", asset["browser_download_url"], headers=headers) as resp:
            if resp.status_code != 200:
                raise UpdateError(f"{asset['name']} : GitHub a répondu {resp.status_code}.")
            with open(part, "wb") as out:
                async for chunk in resp.aiter_bytes(CHUNK):
                    out.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    progress(done)
    if done != asset["size"] or (digest and h.hexdigest() != digest):
        part.unlink(missing_ok=True)
        raise UpdateError(f"{asset['name']} : fichier incomplet ou abîmé (empreinte SHA-256 différente).")
    part.replace(target)
    return target


def _extract(zips, dest, manifest):
    if dest.exists():
        shutil.rmtree(dest)
    with zipfile.ZipFile(zips[0]) as z:
        z.extractall(dest)
    exe = next(dest.rglob("llama-server.exe"), None)
    if exe is None:
        shutil.rmtree(dest, ignore_errors=True)
        raise UpdateError(f"llama-server.exe introuvable dans {zips[0].name}.")
    for extra in zips[1:]:
        with zipfile.ZipFile(extra) as z:
            z.extractall(exe.parent)  # les DLL CUDA doivent être à côté de llama-server.exe
    (dest / "engine.json").write_text(json.dumps(manifest, indent=4), encoding="utf-8")


def _swap(name, new):
    """La version neuve prend la place de l'actuelle, qui devient la précédente (l'ancienne précédente part).
    Si un renommage échoue (dossier utilisé), tout est remis en place."""
    cur, prev, old = engine_dir(name), _side(name, ".prev"), _side(name, ".old")
    if old.exists():
        shutil.rmtree(old)
    if prev.exists():
        prev.rename(old)
    try:
        if cur.exists():
            cur.rename(prev)
        new.rename(cur)
    except OSError:
        if not cur.exists() and prev.exists():
            prev.rename(cur)
        if old.exists() and not prev.exists():
            old.rename(prev)
        raise
    shutil.rmtree(old, ignore_errors=True)


async def install(name, release, backend_name, progress=None, in_use=None):
    """Télécharge la release, l'extrait à côté et l'échange avec la version en place."""
    llama, cudart = BACKENDS[backend_name]
    assets = [find_asset(release, llama)] + ([find_asset(release, cudart)] if cudart else [])
    if None in assets:
        raise UpdateError(f"La release {release.get('tag_name')} n'a pas de zip pour {backend_name}.")
    total, before, zips = sum(a["size"] for a in assets), 0, []
    for asset in assets:
        zips.append(await _download(asset, lambda n, base=before: progress and progress(base + n, total)))
        before += asset["size"]
    new = _side(name, ".new")
    manifest = {"repo": ENGINES[name]["repo"], "tag": release["tag_name"], "backend": backend_name}
    await asyncio.to_thread(_extract, zips, new, manifest)
    if in_use and in_use(name):
        shutil.rmtree(new, ignore_errors=True)
        raise UpdateError("Un modèle a été chargé sur ce moteur entre-temps : éjecte-le, puis relance la mise à jour.")
    try:
        await asyncio.to_thread(_swap, name, new)
    except OSError as exc:
        shutil.rmtree(new, ignore_errors=True)
        raise UpdateError(f"Le dossier du moteur est utilisé (un llama-server lancé à côté ?) : {exc}")
    zips[0].unlink(missing_ok=True)  # le zip de la build ne resservira pas ; le runtime CUDA, si


def busy(name):
    """La mise à jour de ce moteur est en cours : pas de chargement pendant ce temps."""
    return bool(_job and _job["name"] == name and _job["status"] in ("downloading", "installing"))


async def update(name, in_use):
    """Lance en tâche de fond la mise à jour vers la dernière release trouvée ; in_use(name) : le moteur tourne."""
    global _job
    if _job and _job["status"] in ("downloading", "installing"):
        raise UpdateError("Une mise à jour de moteur est déjà en cours.")
    if in_use(name):
        raise UpdateError("Ce moteur fait tourner le modèle chargé : éjecte-le d'abord.")
    backend_name = backend(name)
    release = (_latest.get(name) or {}).get("release") or await latest(name, backend_name)
    _job = {"name": name, "tag": release["tag_name"], "status": "downloading", "done_bytes": 0, "total_bytes": 0,
            "error": None}
    job = _job

    def progress(done, total):
        job.update(done_bytes=done, total_bytes=total, status="downloading" if done < total else "installing")

    async def run():
        try:
            await install(name, release, backend_name, progress, in_use)
            job["status"] = "done"
        except (UpdateError, httpx.HTTPError, OSError, zipfile.BadZipFile) as exc:
            job.update(status="error", error=str(exc) or exc.__class__.__name__)

    asyncio.create_task(run())
    return dict(job)


def rollback(name, in_use):
    """Échange la version en place et la précédente (on peut donc revenir de nouveau)."""
    if in_use(name) or busy(name):
        raise UpdateError("Ce moteur est utilisé : éjecte le modèle (ou attends la fin de la mise à jour).")
    cur, prev, tmp = engine_dir(name), _side(name, ".prev"), _side(name, ".old")
    if not find_server_in(prev):
        raise UpdateError("Aucune version précédente gardée pour ce moteur.")
    if tmp.exists():
        shutil.rmtree(tmp)
    try:
        cur.rename(tmp)
        prev.rename(cur)
        tmp.rename(prev)
    except OSError as exc:
        if not cur.exists() and tmp.exists():
            tmp.rename(cur)
        raise UpdateError(f"Le dossier du moteur est utilisé (un llama-server lancé à côté ?) : {exc}")
    return info(name)
