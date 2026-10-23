"""Recherche de mises à jour : le plus grand tag vX.Y.Z du dépôt GitHub, comparé à la version installée.

Rien ne s'installe tout seul (Aria se construit depuis les sources) : on dit seulement qu'une version existe.
Une seule requête GET anonyme, sans aucune donnée envoyée, au plus une fois par 24 h sauf demande explicite.
"""

import asyncio
import re
import time

import httpx

from . import VERSION

REPO = "ZedjeK/Aria-Pilote"
TAGS_URL = f"https://api.github.com/repos/{REPO}/tags?per_page=100"
REPO_URL = f"https://github.com/{REPO}"
TAG_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
MAX_AGE_S = 24 * 3600

_lock = asyncio.Lock()
_cache = {"checked_at": None, "latest": None, "error": None}


def parse(version):
    match = TAG_RE.match(version.strip())
    return tuple(int(n) for n in match.groups()) if match else None


def latest_tag(names):
    """Le plus grand tag de version parmi des noms de tags (les autres sont ignorés)."""
    versions = [v for v in (parse(n) for n in names) if v]
    return ".".join(map(str, max(versions))) if versions else None


def status():
    current = parse(VERSION)
    latest = _cache["latest"]
    newer = bool(latest and current and parse(latest) > current)
    return {
        "current": VERSION,
        "latest": latest,
        "newer": newer,
        # Plus récente que tout ce qui est publié : la version de développement
        "dev": bool(latest and current and current > parse(latest)),
        "checked_at": _cache["checked_at"],
        "error": _cache["error"],
        "changes_url": f"{REPO_URL}/compare/v{VERSION}...v{latest}" if newer else None,
        "repo_url": REPO_URL,
    }


async def _fetch_tags():
    headers = {"Accept": "application/vnd.github+json", "User-Agent": f"AriaPilote/{VERSION}"}
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        resp = await client.get(TAGS_URL, headers=headers)
    if resp.status_code == 403:
        raise RuntimeError("GitHub limite les requêtes pour l'instant : réessaie plus tard.")
    if resp.status_code != 200:
        raise RuntimeError(f"GitHub a répondu {resp.status_code}.")
    return [t.get("name", "") for t in resp.json()]


async def check(force=False):
    """Interroge GitHub si la dernière réponse a plus de 24 h (ou si `force`) ; renvoie l'état."""
    async with _lock:
        fresh = _cache["checked_at"] and time.time() - _cache["checked_at"] < MAX_AGE_S
        if force or not fresh:
            try:
                _cache["latest"] = latest_tag(await _fetch_tags())
                _cache["error"] = None
            except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                _cache["error"] = str(exc) if isinstance(exc, RuntimeError) else "Impossible de joindre GitHub."
            _cache["checked_at"] = time.time()
    return status()
