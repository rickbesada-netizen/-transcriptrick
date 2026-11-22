"""HTTP API and web page."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__, audio, db, downloads, exports, sources, summarize, worker
from .config import ffmpeg_exe, load_settings, public_settings, save_settings
from .timing import paragraphs

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_app):
    db.init()
    worker.start()
    downloads.start()
    yield


app = FastAPI(title="Transcriptrick", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)
# Only this PC's own address is answered, so other websites can't reach it through the browser.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])


class ResolveBody(BaseModel):
    text: str
    mode: str = "transcribe"


class DownloadBody(BaseModel):
    collections: list[Collection]
    format: str = "mp4"
    quality: str = "1080"


class Collection(BaseModel):
    title: str | None = None
    url: str | None = None
    source: str | None = None
    is_collection: bool = False
    items: list[dict]


class JobsBody(BaseModel):
    collections: list[Collection]


class StyleBody(BaseModel):
    style: str = "overview"


class AskBody(BaseModel):
    question: str
    history: list[dict] = []


@app.get("/")
def page():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/status")
def status():
    settings = load_settings()
    return {"version": __version__, "ai_ready": summarize.ready(settings),
            "ai_label": summarize.model_label(settings), "transcriber": audio.active_transcriber(settings),
            "ffmpeg": bool(ffmpeg_exe()),
            "styles": [{"id": k, "label": v["label"]} for k, v in summarize.STYLES.items()],
            "providers": {k: {"label": v["label"], "model": v["model"], "key": v["key"]}
                          for k, v in summarize.PROVIDERS.items()}}


@app.post("/api/resolve")
def resolve(body: ResolveBody):
    links = sources.find_links(body.text)
    if not links:
        raise HTTPException(400, "Paste a video or playlist link." if body.mode == "download"
                            else "Paste a YouTube, Spotify or SoundCloud link.")
    settings = load_settings()
    found, errors = [], []
    pick = downloads.resolve_any if body.mode == "download" else sources.resolve
    for url in links[:50]:
        try:
            found.append(pick(url, settings))
        except sources.LinkError as exc:
            errors.append({"url": url, "error": str(exc)})
        except Exception as exc:
            errors.append({"url": url, "error": f"Couldn't read this link: {exc}"[:300]})
    return {"collections": found, "errors": errors}


@app.post("/api/jobs")
def add_jobs(body: JobsBody):
    ids = []
    for c in body.collections:
        items = [i for i in c.items if sources.source_of(str(i.get("url") or "")) and i.get("source") in
                 ("youtube", "soundcloud", "spotify")]
        if items:
            ids.append(db.create_batch(c.title or items[0].get("title"), c.url, c.source, c.is_collection, items))
    if not ids:
        raise HTTPException(400, "Nothing was selected.")
    worker.wake()
    return {"batches": ids}


@app.get("/api/library")
def library():
    flags = db.summary_flags()
    return {"batches": db.library(), "summaries": flags}


def job_or_404(job_id: str) -> dict:
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, "This item no longer exists.")
    return job


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: str):
    job = job_or_404(job_id)
    lyric = "lyrics" in (job.get("method") or "").lower()
    job["paragraphs"] = paragraphs(job["segments"], lyric)
    job.pop("segments")
    job.pop("text", None)
    job["line_by_line"] = lyric
    job["summaries"] = db.get_summaries(job_id)
    return job


@app.post("/api/jobs/{job_id}/cancel")
def cancel(job_id: str):
    job = job_or_404(job_id)
    if job["status"] in ("queued", "working"):
        db.update_job(job_id, status="cancelled", stage="Stopped")
    return {"ok": True}


@app.post("/api/jobs/{job_id}/retry")
def retry(job_id: str):
    job_or_404(job_id)
    db.update_job(job_id, status="queued", stage="Waiting", progress=0, error=None)
    worker.wake()
    return {"ok": True}


@app.delete("/api/jobs/{job_id}")
def remove(job_id: str):
    job = job_or_404(job_id)
    if job["status"] == "working":
        db.update_job(job_id, status="cancelled")
    db.delete_job(job_id)
    return {"ok": True}


@app.post("/api/jobs/{job_id}/summary")
def make_summary(job_id: str, body: StyleBody):
    job = job_or_404(job_id)
    if job["status"] != "done":
        raise HTTPException(409, "The transcript isn't ready yet.")
    settings = load_settings()
    if not summarize.ready(settings):
        raise HTTPException(400, "Add an AI key in Settings first.")
    if body.style not in summarize.STYLES:
        raise HTTPException(400, "Unknown summary style.")
    worker.queue_summary(job_id, body.style)
    return {"ok": True}


@app.post("/api/jobs/{job_id}/ask")
def ask(job_id: str, body: AskBody):
    job = job_or_404(job_id)
    if job["status"] != "done":
        raise HTTPException(409, "The transcript isn't ready yet.")
    done = [s for s in db.get_summaries(job_id) if s["status"] == "done"]
    try:
        answer = summarize.ask(job, body.question, body.history, done[0]["content"] if done else "", load_settings())
    except summarize.AIError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"answer": answer}


def attachment(content, media: str, filename: str) -> Response:
    return Response(content, media_type=media,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


@app.get("/api/jobs/{job_id}/export/{fmt}")
def export(job_id: str, fmt: str):
    job = job_or_404(job_id)
    if fmt not in exports.FORMATS:
        raise HTTPException(404, "Unknown format.")
    if job["status"] != "done":
        raise HTTPException(409, "The transcript isn't ready yet.")
    ext = exports.EXTENSIONS.get(fmt, fmt)
    return attachment(exports.render(job, db.get_summaries(job_id), fmt), exports.FORMATS[fmt],
                      f"{exports.safe_name(job['title'])}.{ext}")


def batch_or_404(batch_id: str) -> dict:
    batch = db.get_batch(batch_id)
    if not batch:
        raise HTTPException(404, "This collection no longer exists.")
    return batch


@app.get("/api/batches/{batch_id}")
def batch_detail(batch_id: str):
    batch = batch_or_404(batch_id)
    for j in batch["jobs"]:
        j.pop("segments", None)
        j.pop("text", None)
    batch["summaries"] = db.get_summaries(batch_id)
    return batch


@app.post("/api/batches/{batch_id}/summary")
def batch_summary(batch_id: str):
    batch = batch_or_404(batch_id)
    if not summarize.ready(load_settings()):
        raise HTTPException(400, "Add an AI key in Settings first.")
    if not any(j["status"] == "done" for j in batch["jobs"]):
        raise HTTPException(409, "No transcripts are ready yet.")
    worker.queue_batch_summary(batch_id)
    return {"ok": True}


@app.post("/api/batches/{batch_id}/retry")
def batch_retry(batch_id: str):
    for job in batch_or_404(batch_id)["jobs"]:
        if job["status"] in ("failed", "cancelled"):
            db.update_job(job["id"], status="queued", stage="Waiting", progress=0, error=None)
    worker.wake()
    return {"ok": True}


@app.get("/api/batches/{batch_id}/export")
def batch_export(batch_id: str):
    batch = batch_or_404(batch_id)
    roll = next((s["content"] for s in db.get_summaries(batch_id) if s["status"] == "done"), None)
    data = exports.batch_zip(batch, db.get_summaries, roll)
    return attachment(data, "application/zip", f"{exports.safe_name(batch['title'])}.zip")


@app.delete("/api/batches/{batch_id}")
def batch_delete(batch_id: str):
    batch = batch_or_404(batch_id)
    for job in batch["jobs"]:
        if job["status"] == "working":
            db.update_job(job["id"], status="cancelled")
    db.delete_batch(batch_id)
    return {"ok": True}


@app.get("/api/search")
def search(q: str = ""):
    return {"results": db.search(q.strip()) if len(q.strip()) >= 2 else []}


@app.get("/api/settings")
def get_settings():
    return public_settings()


@app.put("/api/settings")
def put_settings(body: dict):
    return public_settings(save_settings(body))


@app.get("/api/models")
def models():
    try:
        return {"models": summarize.list_models(load_settings())}
    except summarize.AIError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Couldn't load models: {exc}"[:200]) from exc


@app.post("/api/settings/test")
def test_ai():
    settings = load_settings()
    try:
        reply = summarize.chat([{"role": "user", "content": "Reply with the single word: ready"}], settings,
                               system="You answer tersely.", max_tokens=20)
    except summarize.AIError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "reply": reply[:60], "model": summarize.model_label(settings)}


# ---------- Downloads ----------

@app.post("/api/downloads")
def add_downloads(body: DownloadBody):
    fmt = body.format if body.format in ("mp4", "mp3") else "mp4"
    quality = body.quality if body.quality in downloads.QUALITIES else "1080"
    ids = []
    for c in body.collections:
        items = [i for i in c.items if str(i.get("url") or "").startswith("http")]
        if items:
            ids += db.add_downloads(c.title, c.is_collection and len(items) > 1, items, fmt, quality)
    if not ids:
        raise HTTPException(400, "Nothing was selected.")
    save_settings({"download_format": fmt, "download_quality": quality})
    downloads.wake()
    return {"ids": ids}


@app.get("/api/downloads")
def list_downloads():
    return {"downloads": db.list_downloads(), "folder": load_settings()["download_folder"]}


def download_or_404(did: str) -> dict:
    item = db.get_download(did)
    if not item:
        raise HTTPException(404, "This download no longer exists.")
    return item


@app.post("/api/downloads/{did}/cancel")
def cancel_download(did: str):
    if download_or_404(did)["status"] in ("queued", "working"):
        db.update_download(did, status="cancelled", stage="Stopped")
    return {"ok": True}


@app.post("/api/downloads/{did}/retry")
def retry_download(did: str):
    download_or_404(did)
    db.update_download(did, status="queued", stage="Waiting", progress=0, error=None)
    downloads.wake()
    return {"ok": True}


@app.delete("/api/downloads/{did}")
def remove_download(did: str):
    if download_or_404(did)["status"] == "working":
        db.update_download(did, status="cancelled")
    db.delete_download(did)
    return {"ok": True}


@app.post("/api/downloads/clear")
def clear_downloads():
    db.clear_finished_downloads()
    return {"ok": True}


@app.post("/api/downloads/{did}/{action}")
def show_download(did: str, action: str):
    item = download_or_404(did)
    try:
        if action == "reveal":
            downloads.reveal(item["file"])
        elif action == "open":
            downloads.open_file(item["file"])
        else:
            raise HTTPException(404, "Unknown action.")
    except FileNotFoundError as exc:
        raise HTTPException(410, str(exc)) from exc
    return {"ok": True}


@app.post("/api/folder")
def open_folder():
    folder = Path(load_settings()["download_folder"]).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    downloads.reveal(str(folder), folder_only=True)
    return {"ok": True}


app.mount("/static", StaticFiles(directory=STATIC), name="static")
