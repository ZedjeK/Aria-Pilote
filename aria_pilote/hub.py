"""Recherche de modèles GGUF sur Hugging Face et estimation de ce qui tient sur la machine."""

import ctypes
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import PurePosixPath

import httpx

from . import config, engines, models

HF = "https://huggingface.co"
REPO_RE = re.compile(r"^[\w.-]+/[\w.-]+$")

# Modèles brouillons (décodage spéculatif) qui ne se chargent pas seuls. Un nom qui contient
# juste « MTP » (ex. Bonsai PQ2_0-MTP) est un modèle complet avec tête MTP intégrée.
DRAFT_RE = re.compile(r"^mtp[-_]|dflash", re.IGNORECASE)

# Marge pour le cache KV (contexte 8k) et les buffers CUDA
OVERHEAD_BYTES = int(1.5 * 1024**3)


class HubError(Exception):
    pass


@lru_cache(maxsize=1)
def machine_memory():
    """VRAM et RAM totales en octets (None si inconnues)."""
    vram = None
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        ).stdout
        vram = sum(int(line) for line in out.split() if line.strip().isdigit()) * 1024**2 or None
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    ram = None
    if sys.platform == "win32":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        status = MemoryStatus(dwLength=ctypes.sizeof(MemoryStatus))
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            ram = status.ullTotalPhys
    return {"vram": vram, "ram": ram}


def estimate_fit(size):
    mem = machine_memory()
    if not mem["vram"]:
        return "unknown"
    need = size + OVERHEAD_BYTES
    if need <= mem["vram"] * 0.95:
        return "gpu"
    if mem["ram"] and need <= mem["vram"] + mem["ram"] * 0.7:
        return "partial"
    return "too_big"


def download_root():
    return config.path(config.get()["model_dirs"][0])


def local_path(repo, path):
    """Destination locale d'un fichier du dépôt, en refusant tout chemin qui sortirait du dossier."""
    _check_repo(repo)
    rel = PurePosixPath(path)
    if rel.is_absolute() or ".." in rel.parts:
        raise HubError(f"Chemin refusé : {path}")
    return download_root().joinpath(*repo.split("/"), *rel.parts)


def _check_repo(repo):
    # « . » et « .. » passent l'expression régulière : on les refuse explicitement
    if not REPO_RE.match(repo or "") or any(part in (".", "..") for part in repo.split("/")):
        raise HubError("Nom de dépôt invalide (attendu : éditeur/dépôt).")


async def _get_json(url, params=None):
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        resp = await client.get(url, params=params)
    if resp.status_code == 404:
        raise HubError("Introuvable sur Hugging Face.")
    if resp.status_code != 200:
        raise HubError(f"Hugging Face a répondu {resp.status_code}.")
    return resp.json()


async def search(query, limit=30):
    params = [
        ("search", query), ("filter", "gguf"), ("sort", "downloads"), ("direction", "-1"),
        ("limit", str(limit)),
        ("expand[]", "downloads"), ("expand[]", "likes"), ("expand[]", "lastModified"), ("expand[]", "gated"),
    ]
    results = await _get_json(f"{HF}/api/models", params)
    return [
        {
            "repo": m["id"],
            "downloads": m.get("downloads", 0),
            "likes": m.get("likes", 0),
            "updated_at": m.get("lastModified"),
            "gated": bool(m.get("gated")),
        }
        for m in results
    ]


async def tree(repo):
    """Fichiers GGUF du dépôt : [{path, size, sha256}]."""
    _check_repo(repo)
    items = await _get_json(f"{HF}/api/models/{repo}/tree/main", {"recursive": "true"})
    return [
        {"path": it["path"], "size": it.get("lfs", {}).get("size", it.get("size", 0)), "sha256": it.get("lfs", {}).get("oid")}
        for it in items
        if it.get("type") == "file" and it["path"].lower().endswith(".gguf")
    ]


def _role(name):
    if "mmproj" in name.lower():
        return "vision"
    if DRAFT_RE.search(name):
        return "draft"
    return "model"


async def files(repo):
    """Fichiers du dépôt regroupés (les parties d'un modèle découpé forment une seule entrée)."""
    groups = {}
    for f in await tree(repo):
        name = PurePosixPath(f["path"]).name
        shard = models.SHARD_RE.search(name)
        key = f["path"][: -len(shard.group(0))] if shard else f["path"]
        groups.setdefault(key, []).append(f)

    entries = []
    for key, parts in groups.items():
        parts.sort(key=lambda p: p["path"])
        first = PurePosixPath(parts[0]["path"]).name
        match = models.QUANT_RE.search(first)
        quant = match.group(1).upper() if match else None
        size = sum(p["size"] for p in parts)
        downloaded = all(
            (lp := local_path(repo, p["path"])).exists() and lp.stat().st_size == p["size"] for p in parts
        )
        role = _role(first)
        entries.append({
            "key": key,
            "name": first if len(parts) == 1 else f"{first} (+{len(parts) - 1} parties)",
            "paths": [p["path"] for p in parts],
            "size": size,
            "quant": quant,
            "role": role,
            "engine": engines.auto_engine(quant),
            "fit": estimate_fit(size) if role == "model" else None,
            "downloaded": downloaded,
        })

    order = {"model": 0, "vision": 1, "draft": 2}
    entries.sort(key=lambda e: (order[e["role"]], e["size"]))
    mmproj = _recommended_mmproj(entries)
    return {
        "repo": repo,
        "files": entries,
        "recommended_mmproj": mmproj["key"] if mmproj else None,
        "memory": machine_memory(),
        "download_root": str(download_root()),
    }


def _recommended_mmproj(entries):
    # Le Q8_0 est conseillé pour Bonsai 2 (le BF16 peut faire planter llama-server)
    vision = [e for e in entries if e["role"] == "vision"]
    for e in vision:
        if "q8_0" in e["name"].lower():
            return e
    return vision[0] if vision else None
