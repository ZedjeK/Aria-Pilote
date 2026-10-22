"""Essai réel des réglages proposés par l'optimiseur : chargement, mesure de la VRAM et de la vitesse.

Pour chaque modèle : chargement avec les réglages proposés (sans les mémoriser), puis courte génération.
Si le chargement échoue ou laisse moins de MIN_FREE de VRAM, on descend d'un palier et on réessaie.
À la fin (ou à l'arrêt), le modèle qui était chargé l'est de nouveau, avec ses réglages d'origine.
"""

import asyncio
import time
from pathlib import Path

import httpx

from . import chat, config, hardware, models, optimizer
from .server import manager

GIB = 1024 ** 3
MIN_FREE = int(0.5 * GIB)  # VRAM à laisser libre pour Windows et les autres applications
LOAD_TIMEOUT_S = 240
MAX_ATTEMPTS = 4
GEN_TOKENS = 128
PROMPT = ("Explique en un court paragraphe pourquoi on compte les secondes entre l'éclair et le tonnerre "
          "pendant un orage.")

_running = False


class TrialBusy(Exception):
    pass


def is_running():
    return _running


def _vram():
    gpus = hardware._gpus()
    return (sum(g["vram_used"] for g in gpus), sum(g["vram_free"] for g in gpus)) if gpus else (None, None)


async def _load(model, settings):
    """Charge le modèle et attend qu'il soit prêt ; renvoie None si c'est bon, sinon la raison de l'échec."""
    auto = settings.get("engine", "auto") == "auto"
    engine = model.engine_auto if auto else settings["engine"]
    await manager.load(model, settings, engine, auto)
    deadline = time.time() + LOAD_TIMEOUT_S
    while manager.state == "loading" and time.time() < deadline:
        await asyncio.sleep(0.5)
    if manager.state == "ready":
        return None
    if manager.state == "loading":
        return f"chargement trop long (plus de {LOAD_TIMEOUT_S} s)"
    return "le moteur s'est arrêté au chargement (mémoire insuffisante ?)"


async def _generate():
    """Courte génération : vitesses d'écriture et de lecture du prompt mesurées par llama-server."""
    port = config.get()["llama_port"]
    timings = {}
    async with httpx.AsyncClient(timeout=120) as client:
        # Une réponse sans mesures juste après le chargement arrive parfois : on réessaie une fois
        for attempt in range(2):
            r = await client.post(f"http://127.0.0.1:{port}/completion", headers=manager.auth_headers(),
                                  json={"prompt": PROMPT, "n_predict": GEN_TOKENS, "cache_prompt": False})
            r.raise_for_status()
            timings = r.json().get("timings") or {}
            if timings.get("predicted_per_second"):
                break
            await asyncio.sleep(2)
    return timings.get("predicted_per_second"), timings.get("prompt_per_second")


async def _restore(previous):
    try:
        await manager.unload()
        if previous:
            model, settings, engine, auto = previous
            await manager.load(model, settings, engine, auto)
    except Exception as exc:  # le message part dans le journal du moteur
        manager._log(f"[aria] restauration du modèle après l'essai impossible : {exc}")


async def run(items):
    """items : [{"model_id", "settings"}]. Émet la progression (trial_*) au fil de l'essai."""
    global _running
    if _running:
        raise TrialBusy("Un essai est déjà en cours.")
    _running = True
    previous = None
    if manager.model is not None and manager.state in ("loading", "ready"):
        previous = (manager.model, dict(manager._settings or {}), manager.engine, manager._auto)
    try:
        for item in items:
            model = models.get(item["model_id"])
            if model is None:
                continue
            meta = models._read_meta(Path(model.path))
            layers = optimizer._meta(meta, "block_count") if isinstance(meta, dict) else None
            settings = {**config.model_settings(model.key), **item["settings"]}
            yield chat.event({"type": "trial_start", "model_id": model.id})
            result = None
            for attempt in range(MAX_ATTEMPTS):
                await manager.unload()
                await asyncio.sleep(1)  # le temps que la VRAM soit rendue
                used_before, _ = _vram()
                label = f"{settings['ctx_size'] // 1024}k · cache {settings.get('cache_type') or 'f16'}"
                yield chat.event({"type": "trial_step", "model_id": model.id, "message": f"chargement ({label})…"})
                reason = await _load(model, settings)
                used_after, free_after = _vram()
                if reason is None and free_after is not None and free_after < MIN_FREE:
                    reason = f"il ne resterait que {free_after / GIB:.1f} Go de VRAM libre"
                if reason is None:
                    yield chat.event({"type": "trial_step", "model_id": model.id, "message": "génération de test…"})
                    try:
                        tok_s, prompt_s = await _generate()
                    except (httpx.HTTPError, ValueError) as exc:
                        reason = f"génération impossible ({exc})"
                    else:
                        result = {"ok": True, "settings": settings, "tok_s": tok_s, "prompt_tok_s": prompt_s,
                                  "vram_used": used_after - used_before if used_after is not None else None,
                                  "vram_free": free_after, "adjusted": attempt > 0}
                        break
                lower = optimizer.step_down(settings, layers)
                if lower is None:
                    result = {"ok": False, "message": f"aucun réglage ne tient : {reason}"}
                    break
                yield chat.event({"type": "trial_step", "model_id": model.id,
                                  "message": f"↓ {label} ne convient pas : {reason}. Palier inférieur…"})
                settings = lower
            if result is None:
                result = {"ok": False, "message": f"toujours trop juste après {MAX_ATTEMPTS} essais"}
            yield chat.event({"type": "trial_result", "model_id": model.id, **result})
        yield chat.event({"type": "trial_done", "restored": previous[0].name if previous else None})
    finally:
        _running = False
        # Tâche à part : la restauration doit aller au bout même si le flux est coupé (bouton Arrêter)
        asyncio.get_running_loop().create_task(_restore(previous))
