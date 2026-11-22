"""Background processing: one transcription at a time, summaries alongside."""
from __future__ import annotations

import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import audio, captions, db, podcasts, summarize
from .audio import Cancelled, Unavailable
from .config import TEMP_DIR, load_settings
from .timing import clean_segments, plain_text

_wake = threading.Event()
_ai_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ai")


def wake() -> None:
    _wake.set()


def make_ticker(job_id: str):
    last = [0.0]

    def tick(stage: str, share: float | None) -> None:
        now = time.time()
        if now - last[0] < 0.5:
            return
        last[0] = now
        if db.job_status(job_id) != "working":
            raise Cancelled()
        fields = {}
        if stage:
            fields["stage"] = stage
        if share is not None:
            fields["progress"] = round(share, 3)
        db.update_job(job_id, **fields)

    return tick


def transcript_for(job: dict, folder: Path, settings: dict, tick) -> dict:
    source, kind, meta = job["source"], job["kind"], job.get("meta") or {}
    if source == "youtube":
        if settings.get("prefer_captions", True):
            tick("Checking for captions", None)
            found = captions.youtube_captions(job["url"], settings)
            if found:
                return found
        tick("Downloading audio", 0)
        path = audio.download_with_ytdlp(job["url"], folder, settings, tick)
        return audio.transcribe(path, folder, settings, tick)
    if source == "soundcloud":
        tick("Downloading audio", 0)
        path = audio.download_with_ytdlp(job["url"], folder, settings, tick)
        return audio.transcribe(path, folder, settings, tick, speech_only=False)
    if source == "spotify" and kind == "track":
        artist, track = meta.get("artist") or job.get("author") or "", meta.get("track") or job["title"]
        tick("Looking up lyrics", None)
        found = captions.lyrics(artist, track, job.get("duration"), settings)
        if found:
            return found
        tick("Finding this song on YouTube", None)
        path = audio.download_with_ytdlp(f"ytsearch1:{artist} {track} audio", folder, settings, tick)
        result = audio.transcribe(path, folder, settings, tick, speech_only=False)
        result["method"] += ", from a matched YouTube upload"
        return result
    if source == "spotify":
        tick("Finding the podcast's public feed", None)
        url = podcasts.find_episode_audio(job["title"], meta.get("show") or job.get("author") or "", settings)
        if not url:
            raise Unavailable("This episode isn't in any public podcast feed, which usually means it's a Spotify "
                              "exclusive. Spotify's own audio is protected, so it can't be transcribed here.")
        tick("Downloading audio", 0)
        path = audio.download_file(url, folder, settings, tick)
        return audio.transcribe(path, folder, settings, tick)
    raise Unavailable("This kind of link isn't supported.")


def run_job(job: dict) -> None:
    job_id = job["id"]
    settings = load_settings()
    db.update_job(job_id, status="working", stage="Starting", progress=0, error=None)
    tick = make_ticker(job_id)
    folder = Path(tempfile.mkdtemp(prefix="job_", dir=TEMP_DIR))
    try:
        result = transcript_for(job, folder, settings, tick)
        segments = clean_segments(result["segments"])
        if not segments:
            raise Unavailable("No speech was found in this audio.")
        lyric = "lyrics" in result["method"].lower()
        fresh = db.job_status(job_id)
        if fresh != "working":
            return
        db.update_job(job_id, status="done", stage="Done", progress=1, method=result["method"],
                      language=result.get("language") or "", segments=db_json(segments),
                      text=plain_text(segments, lyric), finished=time.time(),
                      duration=job.get("duration") or segments[-1]["end"])
        if settings.get("auto_summary") and summarize.ready(settings):
            queue_summary(job_id, settings.get("default_style") or "overview")
    except Cancelled:
        if db.job_status(job_id) == "working":
            db.update_job(job_id, status="cancelled", stage="Stopped", finished=time.time())
    except Unavailable as exc:
        db.update_job(job_id, status="failed", stage="Failed", error=str(exc), finished=time.time())
    except Exception as exc:  # anything unexpected must not stop the queue
        db.update_job(job_id, status="failed", stage="Failed", error=f"{type(exc).__name__}: {exc}"[:400],
                      finished=time.time())
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    rollup_if_finished(job["batch_id"], settings)


def db_json(value) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


def rollup_if_finished(batch_id: str, settings: dict) -> None:
    batch = db.get_batch(batch_id)
    if not batch or not batch["is_collection"] or not settings.get("auto_summary") or not summarize.ready(settings):
        return
    states = [j["status"] for j in batch["jobs"]]
    if any(s in ("queued", "working") for s in states) or states.count("done") < 2:
        return
    if not any(s["style"] == "collection" for s in db.get_summaries(batch_id)):
        queue_batch_summary(batch_id)


def queue_summary(job_id: str, style: str) -> None:
    db.set_summary(job_id, style, status="pending", error=None)
    _ai_pool.submit(_summarise_job, job_id, style)


def _summarise_job(job_id: str, style: str) -> None:
    db.set_summary(job_id, style, status="working")
    try:
        settings = load_settings()
        content = summarize.summarise_job(db.get_job(job_id), style, settings)
        db.set_summary(job_id, style, status="done", content=content, error=None,
                       model=summarize.model_label(settings))
    except Exception as exc:
        db.set_summary(job_id, style, status="failed", error=str(exc)[:400])


def queue_batch_summary(batch_id: str) -> None:
    db.set_summary(batch_id, "collection", status="pending", error=None)
    _ai_pool.submit(_summarise_batch, batch_id)


def _summarise_batch(batch_id: str) -> None:
    db.set_summary(batch_id, "collection", status="working")
    try:
        settings = load_settings()
        batch = db.get_batch(batch_id)
        parts = []
        for job in batch["jobs"]:
            if job["status"] != "done":
                continue
            done = [s for s in db.get_summaries(job["id"]) if s["status"] == "done"]
            if not done:
                # Wait for an item's summary, or write it here if none is on its way.
                style = settings.get("default_style") or "overview"
                content = summarize.summarise_job(job, style, settings)
                db.set_summary(job["id"], style, status="done", content=content, error=None,
                               model=summarize.model_label(settings))
            else:
                content = done[-1]["content"]
            parts.append((job["title"], content))
        content = summarize.summarise_collection(batch["title"], parts, settings)
        db.set_summary(batch_id, "collection", status="done", content=content, error=None,
                       model=summarize.model_label(settings))
    except Exception as exc:
        db.set_summary(batch_id, "collection", status="failed", error=str(exc)[:400])


def loop() -> None:
    while True:
        job = db.next_queued()
        if job:
            run_job(job)
        else:
            _wake.wait(2)
            _wake.clear()


def start() -> None:
    threading.Thread(target=loop, daemon=True, name="transcriber").start()
