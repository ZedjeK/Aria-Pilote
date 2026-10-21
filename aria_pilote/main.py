"""Application web d'Aria Pilote : API REST + interface statique."""

import re
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import (VERSION, agent, backups, chat, config, conversations, downloads, engines, hub, models, presets,
               searxng, skills, workspace)
from .server import EngineMissing, manager

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app):
    yield
    await manager.shutdown()
    await searxng.manager.stop()


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
    remember: bool = False


class ConfigPatch(BaseModel):
    model_dirs: list[str] | None = None
    load_defaults: dict | None = None


class Preset(BaseModel):
    name: str
    system_prompt: str = ""
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    min_p: float | None = None
    presence_penalty: float | None = None
    max_tokens: int | None = None
    thinking: bool | None = None


class ConversationCreate(BaseModel):
    preset_id: str | None = None


class AgentSettings(BaseModel):
    enabled: bool
    workspace: str | None = None


class ConversationPatch(BaseModel):
    title: str | None = None
    preset_id: str | None = None
    agent: AgentSettings | None = None


class ApprovalRequest(BaseModel):
    approval_id: str
    decision: str  # allow | deny | allow_session


class UndoRequest(BaseModel):
    conversation_id: str
    backup_id: str


class SkillRequest(BaseModel):
    name: str
    description: str
    body: str


class ChatRequest(BaseModel):
    conversation_id: str
    content: str | None = None  # None = régénérer la dernière réponse
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
    settings = config.model_settings(model.path)
    for field in config.LOAD_FIELDS:
        value = getattr(req, field)
        if value is not None:
            settings[field] = value
    if settings["engine"] != "auto" and settings["engine"] not in engines.ENGINES:
        raise HTTPException(400, f"Moteur inconnu : {settings['engine']}")
    if req.remember:
        config.save_model_settings(model.path, settings)
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


@app.get("/api/logs")
def logs(since: int = 0):
    return manager.logs_since(since)


@app.get("/api/config")
def get_config():
    return config.get()


@app.put("/api/config")
def put_config(patch: ConfigPatch):
    cfg = config.update(patch.model_dump(exclude_none=True))
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


@app.post("/api/conversations")
def create_conversation(req: ConversationCreate):
    return conversations.create(req.preset_id)


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
    conversations.delete(cid)
    return {"ok": True}


@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest):
    if manager.state != "ready":
        raise HTTPException(409, "Aucun modèle prêt : charge un modèle dans l'onglet Modèles.")
    conv = _conversation_or_404(req.conversation_id)
    preset = presets.resolve(req.preset_id or conv.get("preset_id") or "default")
    if req.content is not None:
        if not req.content.strip():
            raise HTTPException(400, "Message vide.")
        # « /nom demande » : la compétence est jointe au message si elle existe
        content, skill = req.content, None
        match = re.match(r"^/([a-z0-9][a-z0-9-]*)(?:\s+(.*))?$", req.content.strip(), re.DOTALL)
        if match and match.group(1) in skills.discover(_agent_root(conv)):
            skill, content = match.group(1), (match.group(2) or "").strip()
        conversations.add_user_message(conv, content, skill)
    else:
        conversations.drop_trailing_assistant(conv)
        if not conv["messages"]:
            raise HTTPException(400, "Rien à régénérer.")
    conv["preset_id"] = preset["id"]
    conversations.save(conv)
    use_agent = (conv.get("agent") or {}).get("enabled")
    runner = agent.run if use_agent else chat.stream
    return StreamingResponse(
        runner(conv, preset, manager.model.name),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


def _agent_root(conv):
    """Dossier de travail si le mode Agent est actif (pour trouver les compétences du projet)."""
    settings = (conv or {}).get("agent") or {}
    return settings.get("workspace") if settings.get("enabled") else None


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
        agent.resolve_approval(req.approval_id, req.decision)
    except agent.ApprovalError as exc:
        raise HTTPException(409, str(exc))
    return {"ok": True}


@app.post("/api/agent/undo")
def undo(req: UndoRequest):
    _conversation_or_404(req.conversation_id)
    try:
        meta = agent.undo(req.conversation_id, req.backup_id)
    except backups.BackupError as exc:
        raise HTTPException(404, str(exc))
    return {"ok": True, "path": meta["path"]}


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
