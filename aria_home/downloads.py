"""File de téléchargements depuis Hugging Face : reprise, annulation et vérification SHA-256."""

import asyncio
import hashlib
import time
import uuid

import httpx

from . import hub, models

HF = hub.HF
CHUNK = 1024 * 1024


class Cancelled(Exception):
    pass


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8 * CHUNK):
            h.update(chunk)
    return h.hexdigest()


class Job:
    def __init__(self, repo, files, label):
        self.id = uuid.uuid4().hex[:8]
        self.repo = repo
        self.files = files  # [{path, size, sha256}]
        self.label = label
        self.status = "queued"  # queued | downloading | verifying | done | error | cancelled
        self.error = None
        self.current = None
        self.done_bytes = 0
        self.total_bytes = sum(f["size"] for f in files)
        self.speed = 0.0
        self.cancel_requested = False

    def to_dict(self):
        remaining = self.total_bytes - self.done_bytes
        return {
            "id": self.id,
            "repo": self.repo,
            "label": self.label,
            "status": self.status,
            "error": self.error,
            "current": self.current,
            "done_bytes": self.done_bytes,
            "total_bytes": self.total_bytes,
            "speed": round(self.speed),
            "eta_s": int(remaining / self.speed) if self.status == "downloading" and self.speed > 0 else None,
        }


class DownloadManager:
    def __init__(self):
        self.jobs = {}
        self._queue = asyncio.Queue()
        self._worker = None

    def _ensure_worker(self):
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run_forever())

    async def add(self, repo, paths, label):
        available = {f["path"]: f for f in await hub.tree(repo)}
        missing = [p for p in paths if p not in available]
        if missing:
            raise hub.HubError(f"Fichiers absents du dépôt : {', '.join(missing)}")
        for p in paths:
            hub.local_path(repo, p)  # valide le chemin avant de mettre en file
        job = Job(repo, [available[p] for p in paths], label)
        self.jobs[job.id] = job
        await self._queue.put(job)
        self._ensure_worker()
        return job

    def cancel(self, job_id):
        job = self.jobs[job_id]
        job.cancel_requested = True
        if job.status == "queued":
            job.status = "cancelled"

    async def resume(self, job_id):
        job = self.jobs[job_id]
        if job.status not in ("cancelled", "error"):
            return job
        job.status, job.error, job.cancel_requested = "queued", None, False
        await self._queue.put(job)
        self._ensure_worker()
        return job

    def discard(self, job_id):
        """Retire le téléchargement de la liste et supprime ses fichiers partiels."""
        job = self.jobs[job_id]
        if job.status in ("downloading", "verifying"):
            raise hub.HubError("Annule d'abord le téléchargement en cours.")
        for f in job.files:
            part = self._part(job, f)
            if part.exists():
                part.unlink()
        del self.jobs[job_id]

    def list(self):
        return [job.to_dict() for job in reversed(self.jobs.values())]

    @staticmethod
    def _part(job, f):
        dest = hub.local_path(job.repo, f["path"])
        return dest.with_name(dest.name + ".part")

    async def _run_forever(self):
        while True:
            job = await self._queue.get()
            if job.status != "queued" or job.cancel_requested:
                continue
            try:
                await self._run(job)
                job.status = "done"
                models.scan(force=True)
            except Cancelled:
                job.status = "cancelled"
            except Exception as exc:  # le worker doit survivre à n'importe quel échec
                job.status = "error"
                job.error = str(exc) or exc.__class__.__name__
            finally:
                job.current = None
                job.speed = 0.0

    async def _run(self, job):
        job.status = "downloading"
        job.done_bytes = 0
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=60), follow_redirects=True) as client:
            for f in job.files:
                dest = hub.local_path(job.repo, f["path"])
                if dest.exists() and dest.stat().st_size == f["size"]:
                    job.done_bytes += f["size"]
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                part = self._part(job, f)
                job.current = dest.name
                await self._fetch(client, job, f, part)
                job.status = "verifying"
                if f["sha256"]:
                    digest = await asyncio.to_thread(_sha256, part)
                    if digest != f["sha256"]:
                        part.unlink()
                        raise hub.HubError(f"SHA-256 différent pour {dest.name} : fichier corrompu, supprimé.")
                part.replace(dest)
                job.status = "downloading"

    async def _fetch(self, client, job, f, part):
        start = part.stat().st_size if part.exists() else 0
        if start > f["size"]:
            part.unlink()
            start = 0
        if start == f["size"]:
            job.done_bytes += start
            return
        headers = {"Range": f"bytes={start}-"} if start else {}
        url = f"{HF}/{job.repo}/resolve/main/{f['path']}"
        async with client.stream("GET", url, headers=headers) as resp:
            if resp.status_code == 200:
                start = 0  # le serveur a ignoré la reprise : on repart de zéro
            elif resp.status_code != 206:
                raise hub.HubError(f"Hugging Face a répondu {resp.status_code} pour {f['path']}.")
            base = job.done_bytes
            job.done_bytes = base + start
            window_start, window_bytes = time.monotonic(), 0
            with open(part, "ab" if start else "wb") as out:
                async for chunk in resp.aiter_bytes(CHUNK):
                    if job.cancel_requested:
                        raise Cancelled()
                    out.write(chunk)
                    job.done_bytes += len(chunk)
                    window_bytes += len(chunk)
                    elapsed = time.monotonic() - window_start
                    if elapsed >= 1:
                        job.speed = window_bytes / elapsed
                        window_start, window_bytes = time.monotonic(), 0


manager = DownloadManager()
