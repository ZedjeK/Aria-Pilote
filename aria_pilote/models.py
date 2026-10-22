"""Scan des dossiers de modèles et extraction des infos utiles de chaque GGUF."""

import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from . import config, engines, gguf, trash

QUANT_RE = re.compile(
    r"(?<![A-Z0-9])("
    r"PTQ1_0|PQ2_0|IQ\d_(?:XXS|XS|NL|S|M)|Q\d_K(?:_(?:XL|[SML]))?|Q\d_\d|TQ\d_0|MXFP4|BF16|F16|F32"
    r")(?![A-Z0-9])",
    re.IGNORECASE,
)
SHARD_RE = re.compile(r"-(\d{5})-of-(\d{5})\.gguf$", re.IGNORECASE)


@dataclass
class ModelInfo:
    id: str
    key: str  # chemin relatif au dossier de modèles : stable d'une version d'Aria à l'autre
    path: str
    file: str
    publisher: str
    repo: str
    name: str
    size_bytes: int
    quant: str | None
    arch: str | None
    context_length: int | None
    layers: int | None
    size_label: str | None
    mmproj: str | None
    engine_auto: str
    error: str | None = None


# Cache : chemin -> (mtime, taille, métadonnées ou erreur)
_meta_cache = {}
_models = {}


def _model_id(key):
    return hashlib.sha1(key.lower().encode("utf-8")).hexdigest()[:12]


def _read_meta(path):
    stat = path.stat()
    cached = _meta_cache.get(path)
    if cached and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
        return cached[2]
    try:
        result = gguf.read_metadata(path)["metadata"]
    except (OSError, gguf.GGUFError) as exc:
        result = exc
    _meta_cache[path] = (stat.st_mtime, stat.st_size, result)
    return result


def _is_projector(path, meta):
    if "mmproj" in path.name.lower():
        return True
    return isinstance(meta, dict) and meta.get("general.architecture") == "clip"


def _pick_mmproj(candidates):
    if not candidates:
        return None
    # Le Q8_0 est conseillé pour Bonsai 2 (le BF16 peut faire planter llama-server)
    for c in candidates:
        if "q8_0" in c.name.lower():
            return c
    return sorted(candidates)[0]


def _shard_size(path):
    match = SHARD_RE.search(path.name)
    if not match:
        return path.stat().st_size
    prefix = path.name[: match.start()]
    return sum(p.stat().st_size for p in path.parent.glob(f"{prefix}-*-of-{match.group(2)}.gguf"))


def _display_name(meta_name, path):
    """Nom de l'en-tête GGUF, ou celui du fichier quand l'en-tête est trop pauvre (Bonsai 2 annonce « Hf »)."""
    name = (meta_name or "").strip()
    return name if len(name) >= 4 else path.stem


def _describe(path, root, meta, mmproj, key):
    rel = path.relative_to(root).parts
    publisher, repo = (rel[0], rel[1]) if len(rel) >= 3 else ("local", path.parent.name)
    match = QUANT_RE.search(path.name)
    quant = match.group(1).upper() if match else None
    error = str(meta) if isinstance(meta, Exception) else None
    meta = meta if isinstance(meta, dict) else {}
    arch = meta.get("general.architecture")
    return ModelInfo(
        id=_model_id(key),
        key=key,
        path=str(path),
        file=path.name,
        publisher=publisher,
        repo=repo,
        name=_display_name(meta.get("general.name"), path),
        size_bytes=_shard_size(path),
        quant=quant,
        arch=arch,
        context_length=meta.get(f"{arch}.context_length") if arch else None,
        layers=meta.get(f"{arch}.block_count") if arch else None,
        size_label=meta.get("general.size_label"),
        mmproj=str(mmproj) if mmproj else None,
        engine_auto=engines.auto_engine(quant),
        error=error,
    )


def scan(force=False):
    global _models
    if _models and not force:
        return list(_models.values())
    found, keys = {}, set()
    for index, root in enumerate(config.get()["model_dirs"]):
        root = config.path(root)
        if not root.is_dir():
            continue
        by_folder = {}
        for path in root.rglob("*.gguf"):
            shard = SHARD_RE.search(path.name)
            if shard and shard.group(1) != "00001":
                continue
            by_folder.setdefault(path.parent, []).append(path)
        for folder, paths in by_folder.items():
            metas = {p: _read_meta(p) for p in paths}
            projectors = [p for p in paths if _is_projector(p, metas[p])]
            mmproj = _pick_mmproj(projectors)
            for p in paths:
                if p in projectors:
                    continue
                key = p.relative_to(root).as_posix()
                if key.lower() in keys:  # même chemin relatif dans deux dossiers de modèles
                    key = f"{index}:{key}"
                keys.add(key.lower())
                info = _describe(p, root, metas[p], mmproj, key)
                found[info.id] = info
    config.migrate_model_keys([m.key for m in found.values()])
    _models = dict(sorted(found.items(), key=lambda kv: (kv[1].publisher.lower(), kv[1].name.lower())))
    return list(_models.values())


def get(model_id):
    if not _models:
        scan()
    return _models.get(model_id)


def delete(model_id):
    """Envoie le modèle (et ses parties) à la corbeille, avec le projecteur vision s'il n'en reste plus d'autre.

    Renvoie la liste des fichiers envoyés à la corbeille.
    """
    model = get(model_id)
    if model is None:
        raise KeyError(model_id)
    path = Path(model.path)
    match = SHARD_RE.search(path.name)
    if match:
        prefix = path.name[: match.start()]
        files = sorted(path.parent.glob(f"{prefix}-*-of-{match.group(2)}.gguf"))
    else:
        files = [path]
    others = [p for p in path.parent.glob("*.gguf") if p not in files]
    if others and all("mmproj" in p.name.lower() for p in others):
        files += others  # projecteur devenu orphelin
    trash.to_recycle_bin(files)
    # Dossiers du dépôt puis de l'éditeur, s'ils sont maintenant vides (jamais un dossier de modèles lui-même)
    roots = {config.path(r).resolve() for r in config.get()["model_dirs"]}
    for folder in (path.parent, path.parent.parent):
        if folder.resolve() in roots:
            break
        try:
            folder.rmdir()
        except OSError:
            break
    config.forget_model(model.key)
    scan(force=True)
    return [str(f) for f in files]


def to_dict(model):
    data = asdict(model)
    data["settings"] = config.model_settings(model.key)
    return data
