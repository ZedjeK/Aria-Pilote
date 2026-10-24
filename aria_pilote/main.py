"""Application web d'Aria Pilote : API REST + interface statique."""

import asyncio
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import (VERSION, agent, background, backups, chat, config, context, conversations, downloads, engines, hardware,
               hooks, hub, models, optimizer, permissions, persona, pick_folder, presets, searxng, skills, subagents,
               tools, trial, updates, workspace)
from .server import EngineMissing, manager

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app):
    # Conversations inactives depuis trop longtemps (si tu l'as demandé) → corbeille, avant le ménage des sauvegardes
    days = config.get().get("conversation_retention_days", 0)
    if days:
        try:
            gone = await asyncio.to_thread(lambda: conversations.to_trash(conversations.inactive_since(days)))
            if gone:
                print(f"{len(gone)} conversation(s) inactive(s) depuis plus de {days} jours envoyée(s) à la corbeille.")
        except OSError as exc:
            print(f"Ménage des conversations impossible : {exc}")
    # Ménage des sauvegardes de l'agent : conversations disparues et sauvegardes trop anciennes → corbeille
    try:
        await asyncio.to_thread(backups.cleanup, conversations.ids(), config.get().get("backup_retention_days", 30))
    except OSError as exc:
        print(f"Ménage des sauvegardes impossible : {exc}")
    autoload = asyncio.create_task(_autoload())
    # En tâche de fond : le démarrage n'attend jamais GitHub
    update = asyncio.create_task(updates.check()) if config.get().get("update_check", True) else None
    idle = asyncio.create_task(searxng.manager.watch_idle())
    yield
    autoload.cancel()
    idle.cancel()
    if update:
        update.cancel()
    await asyncio.to_thread(background.stop_all)  # le job object les tuerait de toute façon : ici, proprement
    await manager.shutdown()
    await searxng.manager.stop()


async def _autoload():
    """Charge le modèle par défaut au démarrage, si « Charger au démarrage » est coché dans les réglages."""
    cfg = config.get()
    if not (cfg.get("autoload") and cfg.get("default_model")):
        return
    await asyncio.to_thread(models.scan)
    model = models.get(cfg["default_model"])
    if model is None:
        print("Modèle par défaut introuvable : rien n'est chargé au démarrage.")
        return
    settings = config.model_settings(model.key)
    auto = settings["engine"] == "auto"
    try:
        await manager.load(model, settings, model.engine_auto if auto else settings["engine"], auto)
    except Exception as exc:  # le démarrage de l'appli ne doit jamais en dépendre
        print(f"Chargement du modèle par défaut impossible : {exc}")


app = FastAPI(title="Aria Pilote", lifespan=lifespan)

# Serveur uvicorn en cours, renseigné par __main__ (None si lancé autrement)
uvicorn_server = None


@app.middleware("http")
async def local_only(request: Request, call_next):
    """N'accepte que l'interface d'Aria Pilote elle-même.

    - Hôte : bloque le « DNS rebinding » (un site qui se ferait passer pour 127.0.0.1
      pour lire tes conversations).
    - Origine : bloque les requêtes envoyées par une autre page ouverte dans ton
      navigateur (arrêter l'appli, éjecter le modèle, valider un accord…).
    """
    port = config.get()["app_port"]
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    if request.headers.get("host") not in hosts:
        return PlainTextResponse("Hôte refusé.", status_code=403)
    origin = request.headers.get("origin")
    if request.method not in ("GET", "HEAD", "OPTIONS") and origin and origin not in {f"http://{h}" for h in hosts}:
        return PlainTextResponse("Origine refusée.", status_code=403)
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        # Après une mise à jour, le navigateur doit reprendre la nouvelle interface (revérification par ETag)
        response.headers["Cache-Control"] = "no-cache"
    return response


class LoadRequest(BaseModel):
    model_id: str
    engine: str | None = None
    ctx_size: int | None = None
    gpu_layers: int | None = None
    threads: int | None = None
    flash_attn: str | None = None
    use_mmproj: bool | None = None
    cache_type: str | None = None
    remember: bool = False


class NewAgentSettings(BaseModel):
    workspace: str | None = None
    enabled: bool | None = None
    auto_web: bool | None = None


class WebSettings(BaseModel):
    enabled: bool | None = None
    results: int | None = Field(None, ge=3, le=20)
    language: str | None = None
    safesearch: int | None = Field(None, ge=0, le=2)


class NotificationSettings(BaseModel):
    enabled: bool | None = None
    only_background: bool | None = None
    task: bool | None = None
    waiting: bool | None = None
    model: bool | None = None
    download: bool | None = None


class ConfigPatch(BaseModel):
    model_dirs: list[str] | None = None
    load_defaults: dict | None = None
    default_model: str | None = None
    autoload: bool | None = None
    default_preset: str | None = None
    default_persona: str | None = None
    new_conversation: NewAgentSettings | None = None
    always_plan: bool | None = None
    max_tool_calls: int | None = Field(None, ge=10, le=100)
    auto_check: bool | None = None
    web: WebSettings | None = None
    searxng_idle_minutes: int | None = Field(None, ge=0, le=1440)
    backup_retention_days: int | None = Field(None, ge=0, le=3650)
    conversation_retention_days: int | None = Field(None, ge=0, le=3650)
    update_check: bool | None = None
    notifications: NotificationSettings | None = None


class Preset(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    temperature: float | None = Field(None, ge=0, le=2)
    top_p: float | None = Field(None, ge=0, le=1)
    top_k: int | None = Field(None, ge=0, le=1000)
    min_p: float | None = Field(None, ge=0, le=1)
    presence_penalty: float | None = Field(None, ge=-2, le=2)
    max_tokens: int | None = Field(None, ge=1, le=131072)
    thinking: bool | None = None
    thinking_budget: int | None = Field(None, ge=0, le=131072)  # tokens de réflexion au plus (None : sans limite)


class ConversationCreate(BaseModel):
    preset_id: str | None = None
    persona_id: str | None = None  # absent : le persona par défaut ; « none » : sans persona
    agent: NewAgentSettings | None = None  # absent : les réglages de départ (Réglages › Comportement)


class AgentSettings(BaseModel):
    enabled: bool  # dossier attaché ou non
    workspace: str | None = None


class ConversationPatch(BaseModel):
    title: str | None = None
    preset_id: str | None = None
    persona_id: str | None = None
    agent: AgentSettings | None = None


class ApprovalRequest(BaseModel):
    approval_id: str
    decision: str  # allow | deny | allow_session | allow_rule
    rule: str | None = None  # allow_rule : la commande exacte, ou un début de commande terminé par *


class UndoRequest(BaseModel):
    conversation_id: str
    backup_id: str


class UndoTaskRequest(BaseModel):
    conversation_id: str
    task_id: str
    force: bool = False  # restaurer aussi les fichiers retouchés depuis la tâche


class SkillRequest(BaseModel):
    name: str
    description: str
    body: str


class ChatRequest(BaseModel):
    conversation_id: str
    content: str | None = None  # None = régénérer la dernière réponse
    answer: str | None = None  # réponse à la question en attente de l'agent ("" : il décide lui-même)
    continue_task: bool = False  # bouton « Continuer » : le message prolonge la tâche en cours
    preset_id: str | None = None


class DownloadRequest(BaseModel):
    repo: str
    paths: list[str]
    label: str | None = None


# --- Moteur et modèles -------------------------------------------------------

@app.get("/api/status")
def status():
    return {**manager.status(), "version": VERSION}


@app.post("/api/shutdown")
def shutdown():
    """Bouton Quitter : uvicorn s'arrête, et le lifespan éjecte le modèle et coupe SearXNG."""
    if uvicorn_server is None:
        raise HTTPException(409, "Arrêt impossible : Aria Pilote n'a pas été lancée par son lanceur.")
    uvicorn_server.should_exit = True
    return {"ok": True}


@app.get("/api/engines")
def list_engines():
    return engines.all_info() + [searxng.manager.info()]


@app.get("/api/models")
def list_models(refresh: bool = False):
    return [models.to_dict(m) for m in models.scan(force=refresh)]


@app.delete("/api/models/{model_id}")
def delete_model(model_id: str):
    if manager.model and manager.model.id == model_id and manager.state in ("loading", "ready"):
        raise HTTPException(409, "Ce modèle est chargé : éjecte-le avant de le supprimer.")
    try:
        files = models.delete(model_id)
    except KeyError:
        raise HTTPException(404, "Modèle introuvable, relance un scan.")
    except OSError as exc:
        raise HTTPException(500, str(exc))
    return {"ok": True, "trashed": files}


@app.post("/api/load")
async def load(req: LoadRequest):
    model = models.get(req.model_id)
    if model is None:
        raise HTTPException(404, "Modèle introuvable, relance un scan.")
    settings = config.model_settings(model.key)
    for field in config.LOAD_FIELDS:
        value = getattr(req, field)
        if value is not None:
            settings[field] = value
    if settings["engine"] != "auto" and settings["engine"] not in engines.ENGINES:
        raise HTTPException(400, f"Moteur inconnu : {settings['engine']}")
    if settings.get("cache_type", "f16") not in config.CACHE_TYPES:
        raise HTTPException(400, f"Type de cache inconnu : {settings['cache_type']}")
    if req.remember:
        config.save_model_settings(model.key, settings)
    auto = settings["engine"] == "auto"
    engine = model.engine_auto if auto else settings["engine"]
    try:
        await manager.load(model, settings, engine, auto)
    except EngineMissing as exc:
        raise HTTPException(409, str(exc))
    return manager.status()


@app.post("/api/unload")
async def unload():
    await manager.unload()
    return manager.status()


class OptimizeItem(BaseModel):
    model_id: str
    settings: dict


class OptimizeApply(BaseModel):
    items: list[OptimizeItem]


def _meta_of(model):
    meta = models._read_meta(Path(model.path))
    return meta if isinstance(meta, dict) else {}


@app.get("/api/hardware")
def get_hardware():
    return hardware.scan()


@app.post("/api/optimize")
def optimize():
    """Les trois choix (rapide, compromis, contexte) pour chaque modèle, d'après le matériel et la VRAM libre.

    Rien n'est appliqué : c'est l'aperçu de la fenêtre « Optimiser mes modèles »."""
    hw = hardware.scan()
    budget = sum(g["vram_free"] for g in hw["gpus"]) if hw["gpus"] else None
    loaded = manager.model if manager.state in ("loading", "ready") else None
    if budget is not None and loaded is not None:
        # La VRAM du modèle chargé redeviendra libre quand il sera rechargé avec les nouveaux réglages
        budget += optimizer.footprint(loaded, _meta_of(loaded), manager._settings or {})
    # VRAM prise par les autres applications (ce qui reste une fois le modèle d'Aria compté comme libéré)
    others = None
    if hw["gpus"]:
        ours = optimizer.footprint(loaded, _meta_of(loaded), manager._settings or {}) if loaded else 0
        others = max(0, sum(g["vram_used"] for g in hw["gpus"]) - ours)
    found = [(m, _meta_of(m), config.model_settings(m.key)) for m in models.scan()]
    profiles = [{"id": pid, **{k: v for k, v in p.items() if k not in ("cap", "compress")},
                 "plans": [optimizer.plan(m, meta, s, budget, pid, hw["cpu"]["cores"]) for m, meta, s in found]}
                for pid, p in optimizer.PROFILES.items()]
    return {"hardware": hw, "budget": budget, "loaded": loaded.name if loaded else None,
            "others_used": others, "gpu_apps": hardware.gpu_apps(),
            "profiles": profiles, "default": optimizer.DEFAULT_PROFILE}


@app.post("/api/optimize/test")
async def optimize_test(req: OptimizeApply):
    """Essai réel des réglages proposés, en flux : chargement, VRAM, vitesse, palier inférieur si besoin."""
    if backups._running:
        raise HTTPException(409, "L'agent travaille : attends la fin de sa tâche avant de lancer l'essai.")
    if trial.is_running():
        raise HTTPException(409, "Un essai est déjà en cours.")
    if not req.items:
        raise HTTPException(400, "Aucun modèle à essayer.")
    items = [{"model_id": i.model_id, "settings": i.settings} for i in req.items]
    return StreamingResponse(trial.run(items), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.post("/api/optimize/apply")
def optimize_apply(req: OptimizeApply):
    """Mémorise les réglages choisis pour chaque modèle (pris en compte au prochain chargement)."""
    checks = {"ctx_size": lambda v: isinstance(v, int) and v >= 512, "gpu_layers": lambda v: isinstance(v, int) and v >= 0,
              "threads": lambda v: isinstance(v, int) and v >= 0, "cache_type": lambda v: v in config.CACHE_TYPES,
              "flash_attn": lambda v: v in ("auto", "on", "off")}
    applied = []
    for item in req.items:
        model = models.get(item.model_id)
        if model is None:
            raise HTTPException(404, f"Modèle introuvable : {item.model_id}")
        bad = [k for k, v in item.settings.items() if k not in checks or not checks[k](v)]
        if bad:
            raise HTTPException(400, f"Réglage invalide pour {model.name} : {', '.join(bad)}")
        config.save_model_settings(model.key, {**config.model_settings(model.key), **item.settings})
        applied.append(model.name)
    return {"applied": applied}


@app.get("/api/logs")
def logs(since: int = 0):
    return manager.logs_since(since)


@app.get("/api/config")
def get_config():
    return config.get()


@app.put("/api/config")
def put_config(patch: ConfigPatch):
    data = patch.model_dump(exclude_none=True)
    if "default_preset" in data and presets.get(data["default_preset"]) is None:
        raise HTTPException(400, "Preset introuvable.")
    if "default_persona" in data and persona.get(data["default_persona"]) is None:
        raise HTTPException(400, "Persona introuvable.")
    language = data.get("web", {}).get("language")
    if language is not None and language not in config.WEB_LANGUAGES:
        raise HTTPException(400, f"Langue inconnue : {language}")
    new = data.get("new_conversation")
    if new is not None:
        if new.get("workspace"):
            try:
                new["workspace"] = str(workspace.validate_root(new["workspace"]))
            except workspace.WorkspaceError as exc:
                raise HTTPException(400, str(exc))
        new.pop("enabled", None)  # le dossier proposé ne s'attache jamais tout seul
    cfg = config.update(data)
    if patch.model_dirs is not None:
        models.scan(force=True)
    return cfg


# --- Presets -------------------------------------------------------------------

@app.get("/api/presets")
def list_presets():
    return presets.list_all()


@app.post("/api/presets")
def create_preset(preset: Preset):
    return presets.create(preset.model_dump())


@app.put("/api/presets/{pid}")
def update_preset(pid: str, preset: Preset):
    updated = presets.update(pid, preset.model_dump())
    if updated is None:
        raise HTTPException(404, "Preset introuvable.")
    return updated


@app.delete("/api/presets/{pid}")
def delete_preset(pid: str):
    try:
        presets.delete(pid)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if config.get().get("default_preset") == pid:
        config.update({"default_preset": presets.list_all()[0]["id"]})  # le défaut supprimé : le premier restant
    return {"ok": True}


# --- Conversations et chat -----------------------------------------------------

def _conversation_or_404(cid):
    conv = conversations.get(cid)
    if conv is None:
        raise HTTPException(404, "Conversation introuvable.")
    return conv


@app.get("/api/conversations")
def list_conversations():
    return conversations.list_all()


def _initial_agent(wanted):
    """Réglages d'une nouvelle conversation : dossier attaché (« enabled ») seulement si on le demande explicitement."""
    root = None
    if wanted.get("enabled") or wanted.get("workspace"):
        if not wanted.get("workspace"):
            raise HTTPException(400, "Choisis d'abord un dossier de travail.")
        try:
            root = str(workspace.validate_root(wanted["workspace"]))
        except workspace.WorkspaceError as exc:
            raise HTTPException(400, str(exc))
    agent = {"enabled": bool(wanted.get("enabled")) and root is not None, "workspace": root,
             "auto_web": bool(wanted.get("auto_web")), "auto_write": False}
    return agent if root or agent["auto_web"] else None


@app.post("/api/conversations")
def create_conversation(req: ConversationCreate):
    if req.agent is not None:
        agent_settings = _initial_agent(req.agent.model_dump(exclude_none=True))
    else:
        # Réglages de départ : l'accès web d'office, jamais le dossier proposé (il ne s'attache pas tout seul)
        agent_settings = _initial_agent({"auto_web": config.get()["new_conversation"].get("auto_web")})
    conv = conversations.create(req.preset_id or presets.resolve(config.get().get("default_preset"))["id"])
    if req.persona_id:
        _check_persona(req.persona_id)
        conv["persona_id"] = req.persona_id
    if agent_settings:
        conv["agent"] = agent_settings
    if req.persona_id or agent_settings:
        conv = conversations.save(conv)
    return conv


def _check_persona(pid):
    if pid != "none" and persona.get(pid) is None:
        raise HTTPException(400, "Persona introuvable.")


@app.get("/api/conversations/{cid}")
def get_conversation(cid: str):
    return _conversation_or_404(cid)


@app.patch("/api/conversations/{cid}")
def patch_conversation(cid: str, patch: ConversationPatch):
    conv = _conversation_or_404(cid)
    if patch.title is not None and patch.title.strip():
        conv["title"] = patch.title.strip()
    if patch.preset_id is not None:
        conv["preset_id"] = patch.preset_id
    if patch.persona_id is not None:
        _check_persona(patch.persona_id)
        conv["persona_id"] = patch.persona_id
    if patch.agent is not None:
        settings = conv.setdefault("agent", {})
        if patch.agent.workspace is not None:
            try:
                root = str(workspace.validate_root(patch.agent.workspace))
            except workspace.WorkspaceError as exc:
                raise HTTPException(400, str(exc))
            if root != settings.get("workspace"):
                # Nouveau dossier : l'autorisation globale des modifs ne suit pas
                settings["auto_write"] = False
            settings["workspace"] = root
        if patch.agent.enabled and not settings.get("workspace"):
            raise HTTPException(400, "Choisis d'abord un dossier de travail.")
        settings["enabled"] = patch.agent.enabled
    return conversations.save(conv)


@app.delete("/api/conversations/{cid}")
def delete_conversation(cid: str):
    _conversation_or_404(cid)
    background.stop_conversation(cid)
    conversations.delete(cid)
    try:
        backups.delete_conversation(cid)  # ses sauvegardes partent à la corbeille avec elle
    except OSError:
        pass  # le ménage du prochain démarrage s'en chargera
    return {"ok": True}


@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest):
    if manager.state != "ready":
        raise HTTPException(409, "Aucun modèle prêt : charge un modèle dans l'onglet Modèles.")
    conv = _conversation_or_404(req.conversation_id)
    preset = presets.resolve(req.preset_id or conv.get("preset_id") or config.get().get("default_preset"))
    if req.content is not None and not req.content.strip():
        raise HTTPException(400, "Message vide.")
    question = agent.pending_question(conv)
    if req.answer is not None and question is None:
        raise HTTPException(409, "Aucune question de l'agent n'attend de réponse.")
    if question is not None and (req.answer is not None or req.content is not None):
        # Ta réponse (bouton ou message tapé) devient le résultat d'ask_user, puis l'agent reprend
        agent.answer_question(question, req.answer if req.answer is not None else req.content)
    elif req.content is not None:
        # « /nom demande » : la compétence est jointe au message si elle existe
        content, skill = req.content, None
        match = re.match(r"^/([a-z0-9][a-z0-9-]*)(?:\s+(.*))?$", req.content.strip(), re.DOTALL)
        if match and match.group(1) in skills.discover(_agent_root(conv)):
            skill, content = match.group(1), (match.group(2) or "").strip()
        conversations.add_user_message(conv, content, skill)
        # Un plan attend ta décision : ton message (« modifie le plan… ») le prolonge, le verrou reste en place
        if not req.continue_task and not agent.plan_locked(conv):
            conv["task_id"] = backups.new_task_id()  # nouvelle demande, nouvelle tâche (réponses et reprises la prolongent)
            if config.get().get("always_plan") and agent.attached_root(conv):
                # « Toujours proposer un plan » : verrou dès le début de la tâche, levé par ton accord
                conv["plan"] = {"status": "required", "task_id": conv["task_id"]}
    else:
        conversations.drop_trailing_assistant(conv)
        if not conv["messages"]:
            raise HTTPException(400, "Rien à régénérer.")
    conv["preset_id"] = preset["id"]
    conversations.save(conv)
    # Une seule boucle : sans dossier, une discussion avec des outils qui ne touchent jamais aux fichiers
    return StreamingResponse(
        agent.run(conv, preset, manager.model.name),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


def _agent_root(conv):
    """Dossier attaché à la conversation (pour trouver les compétences du projet)."""
    return agent.attached_root(conv)


class CompactRequest(BaseModel):
    instructions: str = ""


@app.post("/api/conversations/{cid}/compact")
async def compact_conversation(cid: str, req: CompactRequest):
    """Bouton 🗜 Compacter et « /compact » : résume les anciens échanges maintenant, avec une consigne facultative."""
    conv = _conversation_or_404(cid)
    if manager.state != "ready":
        raise HTTPException(409, "Aucun modèle prêt : c'est le modèle chargé qui rédige le résumé.")
    if backups.is_running(cid):
        raise HTTPException(409, "Une réponse est en cours : attends sa fin pour compacter.")
    preset = presets.resolve(conv.get("preset_id") or config.get().get("default_preset"))
    system_prompt, schemas, preset = agent.context_inputs(conv, preset)
    try:
        return await context.compact(cid, preset, system_prompt, schemas, req.instructions[:500])
    except context.NothingToCompact as exc:
        raise HTTPException(409, str(exc))
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise HTTPException(502, f"Le résumé a échoué : {exc}")


class ConversationRef(BaseModel):
    conversation_id: str


@app.post("/api/agent/memory/rename")
def rename_memory(req: ConversationRef):
    """« Renommer en MEMOIRE.md » : l'ancien ARIA.md du dossier attaché prend le nouveau nom (avec ton accord, jamais seul)."""
    conv = _conversation_or_404(req.conversation_id)
    root = agent.attached_root(conv)
    if not root:
        raise HTTPException(400, "Aucun dossier attaché à cette conversation.")
    base = workspace.validate_root(root)
    old, new = base / agent.LEGACY_MEMORY_FILE, base / agent.MEMORY_FILE
    if not old.is_file():
        raise HTTPException(404, "Pas de fichier ARIA.md dans ce dossier.")
    if new.exists():
        raise HTTPException(409, "MEMOIRE.md existe déjà dans ce dossier : fusionne les deux à la main.")
    old.rename(new)
    return {"file": agent.MEMORY_FILE}


class PlanDecision(BaseModel):
    conversation_id: str


@app.post("/api/agent/plan/approve")
def approve_plan(req: PlanDecision):
    """« Approuver et exécuter » : le verrou se lève pour la tâche en cours."""
    conv = _conversation_or_404(req.conversation_id)
    plan = conv.get("plan") or {}
    if plan.get("status") not in ("pending", "required"):
        raise HTTPException(409, "Aucun plan n'attend ta décision.")
    conv["plan"] = {"status": "approved", "task_id": conv.get("task_id")}
    return conversations.save(conv)


@app.post("/api/agent/plan/cancel")
def cancel_plan(req: PlanDecision):
    """« Abandonner le plan » : rien n'est exécuté, le verrou se lève, ton prochain message ouvre une nouvelle tâche."""
    conv = _conversation_or_404(req.conversation_id)
    if not agent.plan_locked(conv):
        raise HTTPException(409, "Aucun plan n'attend ta décision.")
    conv["plan"] = {"status": "cancelled", "task_id": conv.get("task_id")}
    return conversations.save(conv)


# --- Compétences ---------------------------------------------------------------------

def _skills_root(conversation_id):
    return _agent_root(conversations.get(conversation_id)) if conversation_id else None


@app.get("/api/skills")
def list_skills(conversation_id: str | None = None):
    return [skills.to_dict(s) for s in skills.discover(_skills_root(conversation_id)).values()]


@app.get("/api/skills/{name}")
def get_skill(name: str, conversation_id: str | None = None):
    try:
        return skills.to_dict(skills.get(name, _skills_root(conversation_id)), with_body=True)
    except skills.SkillError as exc:
        raise HTTPException(404, str(exc))


class HooksText(BaseModel):
    content: str


class HooksTrust(BaseModel):
    conversation_id: str


@app.get("/api/hooks")
def get_hooks(conversation_id: str | None = None):
    return {
        "global": hooks.read_global(),
        "global_path": str(hooks.GLOBAL_PATH),
        "project": hooks.project_status(_skills_root(conversation_id)),
        "events": list(hooks.EVENTS),
    }


@app.put("/api/hooks/global")
def put_global_hooks(req: HooksText):
    try:
        hooks.save_global(req.content)
    except hooks.HookError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


@app.post("/api/hooks/trust")
def trust_hooks(req: HooksTrust):
    try:
        hooks.trust(_agent_root(conversations.get(req.conversation_id)))
    except hooks.HookError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


@app.get("/api/subagents")
def list_subagents(conversation_id: str | None = None):
    return [subagents.to_dict(a) for a in subagents.discover(_skills_root(conversation_id)).values()]


@app.post("/api/skills")
def save_skill(req: SkillRequest):
    try:
        return skills.to_dict(skills.save_user_skill(req.name, req.description, req.body))
    except skills.SkillError as exc:
        raise HTTPException(400, str(exc))


@app.delete("/api/skills/{name}")
def delete_skill(name: str):
    try:
        skills.delete_user_skill(name)
    except (skills.SkillError, OSError) as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


@app.post("/api/agent/approve")
def approve(req: ApprovalRequest):
    try:
        agent.resolve_approval(req.approval_id, req.decision, req.rule)
    except agent.ApprovalError as exc:
        raise HTTPException(409, str(exc))
    return {"ok": True}


class RuleDelete(BaseModel):
    conversation_id: str
    rule: str


def _rules_root(conversation_id):
    root = ((_conversation_or_404(conversation_id).get("agent") or {}).get("workspace"))
    if not root:
        raise HTTPException(400, "Aucun dossier de travail pour cette conversation.")
    return root


@app.get("/api/agent/rules")
def list_rules(conversation_id: str):
    """Règles « toujours autoriser » des commandes, pour le dossier de travail de la conversation."""
    return permissions.rules(_rules_root(conversation_id))


@app.delete("/api/agent/rules")
def delete_rule(req: RuleDelete):
    root = _rules_root(req.conversation_id)
    try:
        permissions.remove(root, req.rule)
    except permissions.RuleError as exc:
        raise HTTPException(404, str(exc))
    return permissions.rules(root)


class FolderPick(BaseModel):
    initial: str = ""
    title: str = "Choisir un dossier"


@app.post("/api/pick-folder")
async def pick_folder_endpoint(req: FolderPick):
    """Ouvre la fenêtre « Choisir un dossier » de Windows ; renvoie le chemin choisi, ou null si tu as annulé."""
    try:
        return {"path": await pick_folder.pick(req.initial, req.title[:100])}
    except pick_folder.PickerBusy as exc:
        raise HTTPException(409, str(exc))
    except pick_folder.PickerUnavailable as exc:
        raise HTTPException(501, str(exc))
    except pick_folder.PickerError as exc:
        raise HTTPException(500, str(exc))


# --- Personas et fiche « Toi » ------------------------------------------------------------------

@app.get("/api/persona-catalogue")
def persona_catalogue():
    return persona.CATALOGUE


@app.get("/api/personas")
def list_personas():
    return {"personas": persona.list_all(), "default": persona.default_id()}


@app.post("/api/personas")
def create_persona(data: dict):
    try:
        return persona.create(data)
    except persona.PersonaError as exc:
        raise HTTPException(400, str(exc))


@app.put("/api/personas/{pid}")
def update_persona(pid: str, data: dict):
    try:
        updated = persona.update(pid, data)
    except persona.PersonaError as exc:
        raise HTTPException(400, str(exc))
    if updated is None:
        raise HTTPException(404, "Persona introuvable.")
    return updated


@app.delete("/api/personas/{pid}")
def delete_persona(pid: str):
    try:
        persona.delete(pid)
    except persona.PersonaError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "default": persona.default_id()}


@app.get("/api/personas/{pid}/export")
def export_persona(pid: str):
    try:
        data = persona.export(pid)
    except persona.PersonaError as exc:
        raise HTTPException(404, str(exc))
    filename = re.sub(r"[^\w.-]+", "-", data["name"]).strip("-") or "persona"
    return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="{filename}.persona.json"'})


@app.post("/api/personas/import")
def import_persona(data: dict):
    try:
        created, warnings = persona.import_(data)
    except (persona.PersonaError, TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc))
    return {"persona": created, "warnings": warnings}


@app.get("/api/personas/{pid}/preview")
def preview_persona(pid: str, register: str = "naturel"):
    """« Voir ce que lit le modèle » : le bloc persona pour un registre, et la fiche « Toi »."""
    current = persona.get(pid)
    if current is None:
        raise HTTPException(404, "Persona introuvable.")
    if register not in persona.REGISTERS:
        raise HTTPException(400, "Registre inconnu.")
    text = persona.render(current, register)
    profile = persona.render_profile(persona.get_profile())
    return {"persona": text, "profile": profile, "chars": len(text) + len(profile)}


@app.get("/api/profile")
def get_profile():
    profile = persona.get_profile()
    return {**profile, "preview": persona.render_profile(profile)}


@app.put("/api/profile")
def put_profile(data: dict):
    try:
        profile = persona.save_profile(data)
    except persona.PersonaError as exc:
        raise HTTPException(400, str(exc))
    return {**profile, "preview": persona.render_profile(profile)}


@app.get("/api/update")
async def update_status():
    """État de la recherche de mises à jour (GitHub n'est interrogé que si c'est activé et que la réponse a vieilli)."""
    if config.get().get("update_check", True):
        return await updates.check()
    return updates.status()


@app.post("/api/update/check")
async def update_check_now():
    return await updates.check(force=True)


@app.get("/api/searxng")
def searxng_status():
    return searxng.manager.status()


@app.post("/api/searxng/start")
async def searxng_start():
    if not searxng.manager.installed():
        raise HTTPException(409, "SearXNG n'est pas installé : lance tools/install_searxng.py.")
    await searxng.manager.ensure_running()
    if not await searxng.manager.healthy():
        raise HTTPException(502, "SearXNG ne répond pas : regarde data/searxng.log.")
    return searxng.manager.status()


@app.post("/api/searxng/stop")
async def searxng_stop():
    await searxng.manager.stop()
    return searxng.manager.status()


# --- Données (Réglages › Données) ------------------------------------------------------

def _size(path):
    if path.is_file():
        return path.stat().st_size
    if not path.is_dir():
        return 0
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def _data_places():
    """Ce que la section Données montre et sait ouvrir : une liste fermée, jamais un chemin venu de la page."""
    data = config.ROOT / "data"
    return {"app": config.ROOT, "conversations": conversations.DATA_DIR, "backups": backups.BACKUP_DIR,
            "aria_pilote.log": data / "aria_pilote.log", "searxng.log": data / "searxng.log"}


@app.get("/api/data")
def data_overview():
    places = _data_places()
    cfg = config.get()
    return {
        "app": str(places["app"]),
        "conversations": {"count": len(conversations.ids()), "bytes": _size(places["conversations"]),
                          "retention_days": cfg.get("conversation_retention_days", 0)},
        "backups": {"bytes": _size(places["backups"]), "retention_days": cfg.get("backup_retention_days", 30)},
        "logs": [{"name": name, "bytes": _size(places[name]), "exists": places[name].exists()}
                 for name in ("aria_pilote.log", "searxng.log")],
    }


class DataTarget(BaseModel):
    target: str


@app.post("/api/data/open")
def data_open(req: DataTarget):
    path = _data_places().get(req.target)
    if path is None:
        raise HTTPException(400, "Emplacement inconnu.")
    if not path.exists():
        if path.suffix:
            raise HTTPException(404, "Ce fichier n'existe pas encore.")
        path.mkdir(parents=True, exist_ok=True)
    if not hasattr(os, "startfile"):
        raise HTTPException(501, f"Ouverture possible seulement sous Windows : {path}")
    os.startfile(path)  # l'Explorateur pour un dossier, l'éditeur par défaut pour un journal
    return {"ok": True}


@app.post("/api/data/conversations/trash")
def data_trash_conversations():
    cids = sorted(conversations.ids())
    if any(backups.is_running(cid) for cid in cids):
        raise HTTPException(409, "Une tâche de l'agent est en cours : attends sa fin ou arrête-la d'abord.")
    for cid in cids:
        background.stop_conversation(cid)
    try:
        gone = conversations.to_trash(cids)
        backups.cleanup(conversations.ids(), config.get().get("backup_retention_days", 30))
    except OSError as exc:
        raise HTTPException(500, f"Envoi à la corbeille impossible : {exc}")
    return {"trashed": len(gone), **data_overview()}


@app.post("/api/data/backups/cleanup")
def data_cleanup_backups():
    try:
        count = backups.cleanup(conversations.ids(), config.get().get("backup_retention_days", 30))
    except OSError as exc:
        raise HTTPException(500, f"Ménage impossible : {exc}")
    return {"trashed": count, **data_overview()}


class ProjectRuleDelete(BaseModel):
    root: str
    rule: str


@app.get("/api/rules")
def all_rules():
    """Règles « toujours autoriser » de tous les projets, pour la page Réglages."""
    stored = config.get().get("command_rules", {})
    return [{"root": root, "rules": items} for root, items in sorted(stored.items()) if items]


@app.delete("/api/rules")
def delete_project_rule(req: ProjectRuleDelete):
    try:
        permissions.remove(req.root, req.rule)
    except permissions.RuleError as exc:
        raise HTTPException(404, str(exc))
    return all_rules()


@app.post("/api/agent/undo")
def undo(req: UndoRequest):
    _conversation_or_404(req.conversation_id)
    try:
        meta = agent.undo(req.conversation_id, req.backup_id)
    except backups.BackupError as exc:
        raise HTTPException(404, str(exc))
    return {"ok": True, "path": meta["path"]}


def _task_or_409(req_cid, task_id):
    if backups.is_running(req_cid):
        raise HTTPException(409, "L'agent travaille encore dans cette conversation : attends la fin ou arrête-le.")
    if not backups.ID_RE.match(task_id):
        raise HTTPException(400, "Identifiant de tâche invalide.")


class BackgroundStop(BaseModel):
    conversation_id: str
    id: str


@app.get("/api/agent/background")
def list_background(conversation_id: str, all: bool = False, output: bool = False):
    """Commandes de l'agent en arrière-plan : celles qui tournent, ou toutes (all) avec la fin de leur sortie
    (output) pour l'onglet Arrière-plan du panneau latéral."""
    _conversation_or_404(conversation_id)
    if all:
        return background.listing(conversation_id, output=output)
    return background.running(conversation_id)


@app.get("/api/agent/context")
def agent_context(conversation_id: str):
    """Onglet Contexte du panneau : occupation, modèle, réglages de l'agent et tout ce qu'il lit."""
    conv = _conversation_or_404(conversation_id)
    settings = conv.get("agent") or {}
    root = settings.get("workspace") if settings.get("enabled") else None
    try:
        root = str(workspace.validate_root(root)) if root else None
    except workspace.WorkspaceError:
        root = None
    summary = conv.get("summary") or {}
    loaded = manager.model if manager.state in ("loading", "ready") else None
    load = manager._settings or {}
    memory = None
    if root:
        text = agent.load_memory(root)
        name = agent.memory_file(root)
        memory = {"file": name, "chars": len(text), "legacy": name == agent.LEGACY_MEMORY_FILE} if text else None
    # Poids de chaque couche du prompt (caractères ; l'interface les convertit en tokens approximatifs)
    current = persona.for_conversation(conv)
    persona_text, profile_text = persona.prompt_blocks(conv, folder=bool(root))
    preset_for_conv = presets.resolve(conv.get("preset_id") or config.get().get("default_preset"))
    full_prompt = agent.context_inputs(conv, preset_for_conv)[0]
    layers = {"persona": len(persona_text), "profil": len(profile_text), "memoire": memory["chars"] if memory else 0}
    layers["aria"] = max(0, len(full_prompt) - sum(layers.values()))
    try:
        global_hooks = hooks._read(hooks.GLOBAL_PATH)[1] if hooks.GLOBAL_PATH.is_file() else {}
    except hooks.HookError:
        global_hooks = {}
    project = hooks.project_status(root) if root else None
    allowed = agent.allowed_tools(folder=bool(root))
    # + ask_user et propose_plan, + delegate s'il y a des sous-agents utilisables
    tool_count = len(allowed if allowed is not None else tools.TOOLS) + 2 + bool(subagents.usable(root))
    return {
        "usage": {**(conv.get("context") or {}), "summarized": summary.get("upto", 0) if summary.get("text") else 0,
                  "squeeze": conv.get("squeeze", 0)},
        "model": {"name": loaded.name, "state": manager.state, "engine": manager.engine, "ctx_size": load.get("ctx_size"),
                  "cache_type": load.get("cache_type") or "f16", "gpu_layers": load.get("gpu_layers")} if loaded else None,
        "agent": {"enabled": bool(root), "workspace": root, "plan": (conv.get("plan") or {}).get("status")
                  if agent.plan_locked(conv) else None,
                  "auto_write": bool(settings.get("auto_write")), "auto_web": bool(settings.get("auto_web")),
                  "tools": tool_count},
        "memory": memory,
        "persona": {"id": current["id"], "name": current["name"], "avatar": current.get("avatar", ""),
                    "register": persona.register_for(current)} if current else None,
        "layers": layers,
        "skills": [{"name": s.name, "source": s.source} for s in skills.discover(root).values()],
        "subagents": [{"name": a.name, "source": a.source, "tools": a.tools} for a in subagents.discover(root).values()],
        "hooks": {"global": {event: len(rules) for event, rules in global_hooks.items() if rules},
                  "project": {"trusted": project["trusted"], "error": project["error"]} if project else None},
        "rules": permissions.rules(root) if root else [],
    }


@app.post("/api/agent/background/stop")
def stop_background(req: BackgroundStop):
    _conversation_or_404(req.conversation_id)
    try:
        background.stop(background.get(req.conversation_id, req.id))
    except background.BackgroundError as exc:
        raise HTTPException(404, str(exc))
    return background.running(req.conversation_id)


@app.get("/api/agent/changes")
def list_changes(conversation_id: str):
    """Modifications de la conversation, avec leur diff, pour l'onglet Modifications du panneau latéral."""
    return agent.changes(_conversation_or_404(conversation_id))


@app.get("/api/agent/undo-task")
def undo_task_preview(conversation_id: str, task_id: str):
    conv = _conversation_or_404(conversation_id)
    _task_or_409(conversation_id, task_id)
    return agent.task_preview(conv, task_id)


@app.post("/api/agent/undo-task")
def undo_task(req: UndoTaskRequest):
    _conversation_or_404(req.conversation_id)
    _task_or_409(req.conversation_id, req.task_id)
    return agent.undo_task(req.conversation_id, req.task_id, req.force)


# --- Hugging Face et téléchargements ---------------------------------------------

@app.get("/api/hub/search")
async def hub_search(q: str):
    try:
        return await hub.search(q)
    except (hub.HubError, httpx.HTTPError) as exc:
        raise HTTPException(502, f"Recherche impossible : {exc}")


@app.get("/api/hub/files")
async def hub_files(repo: str):
    try:
        return await hub.files(repo)
    except hub.HubError as exc:
        raise HTTPException(400, str(exc))
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Hugging Face injoignable : {exc}")


def _job_or_404(job_id):
    if job_id not in downloads.manager.jobs:
        raise HTTPException(404, "Téléchargement introuvable.")


@app.get("/api/downloads")
def list_downloads():
    return downloads.manager.list()


@app.post("/api/downloads")
async def add_download(req: DownloadRequest):
    if not req.paths:
        raise HTTPException(400, "Aucun fichier choisi.")
    try:
        job = await downloads.manager.add(req.repo, req.paths, req.label or req.paths[0])
    except hub.HubError as exc:
        raise HTTPException(400, str(exc))
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Hugging Face injoignable : {exc}")
    return job.to_dict()


@app.post("/api/downloads/{job_id}/cancel")
def cancel_download(job_id: str):
    _job_or_404(job_id)
    downloads.manager.cancel(job_id)
    return downloads.manager.jobs[job_id].to_dict()


@app.post("/api/downloads/{job_id}/resume")
async def resume_download(job_id: str):
    _job_or_404(job_id)
    return (await downloads.manager.resume(job_id)).to_dict()


@app.delete("/api/downloads/{job_id}")
def discard_download(job_id: str):
    _job_or_404(job_id)
    try:
        downloads.manager.discard(job_id)
    except hub.HubError as exc:
        raise HTTPException(409, str(exc))
    return {"ok": True}


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
