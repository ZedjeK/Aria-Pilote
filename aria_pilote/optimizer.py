"""Calcul des meilleurs réglages de chargement d'un modèle pour le matériel de la machine.

À partir de l'en-tête GGUF (couches, couches d'attention, têtes, dimensions) et de la VRAM disponible :
- toutes les couches sur le GPU si le modèle y tient, sinon le maximum (et ce sera lent) ;
- le plus grand palier de contexte qui tient, sans dépasser celui du modèle ni le plafond du profil ;
- le cache de contexte compressé (q8_0) seulement s'il fait gagner un palier.
Ce sont des estimations (tampons de calcul approximés) : l'essai réel les confirme.
"""

from pathlib import Path

GIB = 1024 ** 3
STEPS = (4096, 8192, 16384, 32768, 65536, 131072, 262144)
PROFILES = {"equilibre": 32768, "max": None}  # plafond de contexte de chaque profil
BYTES = {"f16": 2.0, "q8_0": 34 / 32, "q4_0": 18 / 32}  # octets par valeur du cache
# Tampons de calcul et contexte CUDA. Mesuré sur 4 chargements réels (Qwen 3.8 IQ3 / Bonsai 2 PQ2,
# RTX 4080 SUPER, 16k à 128k) : entre 0,2 et 1,1 Gio au-delà des poids, du projecteur et du cache
OVERHEAD = int(0.6 * GIB)
MARGIN = int(0.4 * GIB)  # marge de sécurité


def _meta(meta, key):
    return meta.get(f"{meta.get('general.architecture')}.{key}")


def kv_values_per_token(meta):
    """Nombre de valeurs du cache (K + V) par token, ou None si l'en-tête ne le permet pas.

    Les architectures hybrides (Qwen 3.5+, Bonsai 2…) n'ont une attention classique qu'une couche sur N
    (full_attention_interval) : les autres ont un état de taille fixe, négligeable ici."""
    layers = _meta(meta, "block_count")
    heads = _meta(meta, "attention.head_count")
    if not layers or not heads:
        return None
    heads = max(heads) if isinstance(heads, list) else heads
    kv_heads = _meta(meta, "attention.head_count_kv") or heads
    kv_heads = max(kv_heads) if isinstance(kv_heads, list) else kv_heads
    head_dim = (_meta(meta, "embedding_length") or 0) // heads
    k_len = _meta(meta, "attention.key_length") or head_dim
    v_len = _meta(meta, "attention.value_length") or head_dim
    interval = _meta(meta, "full_attention_interval")
    attention_layers = layers // interval if isinstance(interval, int) and interval > 1 else layers
    return attention_layers * kv_heads * (k_len + v_len) if k_len and v_len else None


def _mmproj_bytes(model, settings):
    if settings.get("use_mmproj") and model.mmproj:
        try:
            return Path(model.mmproj).stat().st_size
        except OSError:
            return 0
    return 0


def footprint(model, meta, settings):
    """VRAM estimée d'un modèle chargé avec ces réglages (pour compter celle du modèle déjà chargé)."""
    values = kv_values_per_token(meta) or 0
    layers = _meta(meta, "block_count") or 1
    share = min(1.0, (settings.get("gpu_layers") or 0) / layers)
    kv = values * BYTES.get(settings.get("cache_type") or "f16", 2.0) * (settings.get("ctx_size") or 0)
    return int(model.size_bytes * share + _mmproj_bytes(model, settings) + kv * share + OVERHEAD)


def plan(model, meta, settings, budget, profile="equilibre", cpu_cores=None):
    """Réglages proposés pour un modèle. `budget` : octets de VRAM utilisables (None : pas de GPU NVIDIA)."""
    current = {k: settings.get(k) for k in ("ctx_size", "gpu_layers", "cache_type", "flash_attn", "threads")}
    result = {"model_id": model.id, "name": model.name, "current": current, "proposed": dict(current),
              "vram_estimate": None, "notes": [], "changed": False}
    values = kv_values_per_token(meta)
    layers = _meta(meta, "block_count")
    if not values or not layers:
        result["notes"].append("En-tête GGUF incomplet : réglages laissés tels quels.")
        return result
    cap = min(PROFILES.get(profile) or STEPS[-1], model.context_length or STEPS[-1])
    steps = [s for s in STEPS if s <= cap] or [min(cap, STEPS[0])]
    mmproj = _mmproj_bytes(model, settings)
    proposed = result["proposed"]

    if budget is None:
        # Pas de carte NVIDIA : tout sur le processeur, contexte modeste
        proposed.update(gpu_layers=0, ctx_size=min(16384, steps[-1]), cache_type="f16", threads=cpu_cores or 0)
        result["notes"].append("Pas de carte NVIDIA détectée : le modèle tournera sur le processeur (lent).")
    else:
        fixed = model.size_bytes + mmproj + OVERHEAD + MARGIN

        def best(cache):
            fitting = [s for s in steps if fixed + values * BYTES[cache] * s <= budget]
            return fitting[-1] if fitting else None

        f16, q8 = best("f16"), best("q8_0")
        if f16 is None and q8 is None:
            # Le modèle ne tient pas entier : le plus de couches possible sur le GPU, le reste en RAM
            ctx = min(8192, steps[-1])
            usable = budget - mmproj - OVERHEAD - MARGIN - values * BYTES["f16"] * ctx
            on_gpu = max(0, min(layers - 1, int(layers * usable / model.size_bytes)))
            proposed.update(gpu_layers=on_gpu, ctx_size=ctx, cache_type="f16", threads=cpu_cores or 0)
            result["notes"].append(f"Le modèle ne tient pas entier dans la VRAM : {on_gpu}/{layers} couches sur le GPU, "
                                   "le reste en RAM (beaucoup plus lent).")
            result["vram_estimate"] = int(model.size_bytes * on_gpu / layers + mmproj + OVERHEAD
                                          + values * BYTES["f16"] * ctx)
        else:
            cache, ctx = ("q8_0", q8) if q8 and (f16 is None or q8 > f16) else ("f16", f16)
            proposed.update(gpu_layers=99, ctx_size=ctx, cache_type=cache, threads=0)
            if cache != "f16":
                proposed["flash_attn"] = "on"
                result["notes"].append("Cache de contexte compressé (q8_0) : deux fois plus de contexte, "
                                       "quasiment sans perte de qualité.")
            if ctx == cap and PROFILES.get(profile) and ctx < (model.context_length or 0):
                result["notes"].append(f"Plafonné à {cap // 1024}k par le profil Équilibré.")
            result["vram_estimate"] = int(fixed - MARGIN + values * BYTES[cache] * ctx)
    result["changed"] = proposed != current
    return result
