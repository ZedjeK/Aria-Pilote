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
    experts: int | None = None  # modèle MoE : nombre d'experts
    mtp_layers: int | None = None  # couches de prédiction multi-tokens (MTP)


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
        experts=meta.get(f"{arch}.expert_count") if arch else None,
        mtp_layers=meta.get(f"{arch}.nextn_predict_layers") if arch else None,
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
    data["custom"] = config.has_model_settings(model.key)  # réglages propres à ce modèle
    return data


def _number(value):
    return f"{value:,}".replace(",", " ") if isinstance(value, int) else str(value)


def describe(model, meta):
    """L'en-tête GGUF en clair, pour la fiche du modèle : [{"label", "value", "help"}]."""
    arch = model.arch or ""
    get = lambda key: meta.get(f"{arch}.{key}") if arch else None  # noqa: E731
    rows = []

    def add(label, value, help_text=""):
        if value is not None and value != "":
            rows.append({"label": label, "value": value if isinstance(value, str) else _number(value), "help": help_text})

    add("Nom dans le fichier", meta.get("general.name"))
    add("Architecture", arch, "La famille du modèle : elle décide quel moteur sait le lire.")
    add("Taille annoncée", model.size_label, "Nombre de paramètres (27B = 27 milliards).")
    add("Quantification", model.quant, "Précision des poids : plus le chiffre est bas, plus le fichier est léger et la qualité réduite.")
    add("Contexte maximal", get("context_length"), "Longueur de conversation la plus grande prévue par le modèle, en tokens.")
    add("Couches", get("block_count"), "Chaque couche peut aller sur la carte graphique ou rester en RAM (« Couches GPU »).")
    add("Dimension interne", get("embedding_length"))
    add("Têtes d'attention", get("attention.head_count"))
    kv = get("attention.head_count_kv")
    if kv and kv != get("attention.head_count"):
        add("Têtes clé/valeur", kv, "Moins de têtes K/V que d'attention (GQA) : un cache de contexte plus petit.")
    interval = get("full_attention_interval")
    if get("ssm.state_size") and interval:
        add("Attention hybride", f"attention complète une couche sur {interval}, les autres en mémoire d'état",
            "Ces couches à état ne grossissent pas avec le contexte : un long contexte coûte moins de VRAM.")
    experts = get("expert_count")
    if experts:
        used = get("expert_used_count")
        add("Experts (MoE)", f"{experts}{f', {used} actifs par token' if used else ''}",
            "Seule une partie des experts travaille à chaque token : « Experts gardés en RAM » libère de la VRAM.")
    mtp = get("nextn_predict_layers")
    if mtp:
        add("Prédiction multi-tokens", f"{mtp} couche{'s' if mtp > 1 else ''} MTP",
            "Le modèle sait deviner les tokens suivants : l'option « Prédiction multi-tokens » peut accélérer la génération.")
    add("Projecteur vision", Path(model.mmproj).name if model.mmproj else "aucun",
        "Le fichier mmproj qui permet au modèle de voir les images.")
    sampling = [f"{name} {meta[key]:.2g}" if isinstance(meta.get(key), float) else f"{name} {meta[key]}"
                for name, key in (("température", "general.sampling.temp"), ("top-p", "general.sampling.top_p"),
                                  ("top-k", "general.sampling.top_k")) if key in meta]
    if sampling:
        add("Tirage conseillé", " · ".join(sampling), "Ce que l'auteur recommande : à reporter dans un preset si besoin.")
    template = meta.get("tokenizer.chat_template")
    add("Modèle de conversation", "intégré" if template else "absent",
        "Le format des échanges (chat template), lu par le moteur ; sans lui, un format générique est utilisé.")
    if meta.get("quantize.imatrix.file"):
        add("Matrice d'importance", "oui", "Quantification guidée par des exemples (imatrix) : meilleure qualité à taille égale.")
    add("Licence", meta.get("general.license"))
    return rows
