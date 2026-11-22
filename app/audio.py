"""Download audio and turn it into timestamped text."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

import httpx

from .config import MODELS_DIR, ffmpeg_exe
from .sources import UA, client, friendly_ydl_error, ydl_options


class Cancelled(Exception):
    pass


class Unavailable(Exception):
    """A failure with a message that can be shown to the person as it stands."""


Tick = Callable[[str, float | None], None]


def download_with_ytdlp(url: str, folder: Path, settings: dict, tick: Tick) -> Path:
    import yt_dlp

    def hook(d):
        tick("", None)   # raises Cancelled when the job was stopped
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                tick("Downloading audio", min(d.get("downloaded_bytes", 0) / total, 1))

    opts = ydl_options(settings, format="bestaudio[abr<=96]/worstaudio/bestaudio/best", noplaylist=True,
                       outtmpl=str(folder / "audio.%(ext)s"), progress_hooks=[hook],
                       ignore_no_formats_error=False, overwrites=True, concurrent_fragment_downloads=4)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except Cancelled:
        raise
    except Exception as exc:
        if isinstance(exc.__context__, Cancelled):
            raise Cancelled() from exc
        raise Unavailable(friendly_ydl_error(exc)) from exc
    files = [p for p in folder.glob("audio.*") if not p.name.endswith((".part", ".ytdl"))]
    if not files:
        raise Unavailable("The audio couldn't be saved. Try again.")
    return files[0]


def download_file(url: str, folder: Path, settings: dict, tick: Tick) -> Path:
    target = folder / "audio.bin"
    with client(settings, 60) as c, c.stream("GET", url, headers={"User-Agent": UA}) as res:
        res.raise_for_status()
        total = int(res.headers.get("content-length") or 0)
        done = 0
        with open(target, "wb") as fh:
            for chunk in res.iter_bytes(1 << 18):
                fh.write(chunk)
                done += len(chunk)
                tick("Downloading audio", done / total if total else None)
    return target


_model = None
_model_name = None
_model_lock = threading.Lock()


def _load_model(name: str):
    global _model, _model_name
    with _model_lock:
        if _model is None or _model_name != name:
            from faster_whisper import WhisperModel

            threads = max(2, (os.cpu_count() or 4) - 1)
            _model = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=threads,
                                  download_root=str(MODELS_DIR))
            _model_name = name
    return _model


def transcribe_local(path: Path, settings: dict, tick: Tick, speech_only: bool = True) -> dict:
    name = settings.get("whisper_model") or "base"
    tick(f"Loading the {name} speech model", None)
    model = _load_model(name)
    segments, info = model.transcribe(str(path), language=settings.get("language") or None, beam_size=1,
                                      vad_filter=speech_only, condition_on_previous_text=False)
    total = info.duration or 0
    out = []
    began = time.time()
    for seg in segments:
        out.append({"start": seg.start, "end": seg.end, "text": seg.text})
        if total:
            share = min(seg.end / total, 1)
            left = (time.time() - began) * (1 - share) / share if share > 0.02 else None
            note = f"Transcribing, about {_minutes(left)} left" if left else "Transcribing"
            tick(note, share)
        else:
            tick("Transcribing", None)
    return {"segments": out, "language": info.language, "method": f"Whisper {name} on this PC"}


def _minutes(seconds: float) -> str:
    return "1 min" if seconds < 90 else f"{round(seconds / 60)} min"


def split_for_upload(path: Path, folder: Path, seconds: int = 1200) -> list[Path]:
    exe = ffmpeg_exe()
    if not exe:
        raise Unavailable("ffmpeg is missing, so audio can't be prepared for upload.")
    pattern = str(folder / "part%03d.mp3")
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    run = subprocess.run([exe, "-y", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000",
                          "-c:a", "libmp3lame", "-b:a", "48k", "-f", "segment", "-segment_time", str(seconds),
                          "-reset_timestamps", "1", pattern], capture_output=True, text=True, creationflags=flags)
    if run.returncode:
        raise Unavailable("This audio couldn't be read: " + (run.stderr or "").strip()[-200:])
    return sorted(folder.glob("part*.mp3"))


CLOUD = {
    "groq": ("https://api.groq.com/openai/v1/audio/transcriptions", "whisper-large-v3-turbo", "groq_key", "Groq"),
    "openai": ("https://api.openai.com/v1/audio/transcriptions", "whisper-1", "openai_key", "OpenAI"),
}


def transcribe_cloud(path: Path, folder: Path, provider: str, settings: dict, tick: Tick) -> dict:
    endpoint, model, key_field, label = CLOUD[provider]
    tick("Preparing audio", None)
    parts = split_for_upload(path, folder)
    segments, language = [], ""
    with httpx.Client(timeout=300) as c:
        for i, part in enumerate(parts):
            tick(f"Transcribing with {label}", i / len(parts))
            data = {"model": model, "response_format": "verbose_json"}
            if settings.get("language"):
                data["language"] = settings["language"]
            for attempt in range(6):
                with open(part, "rb") as fh:
                    res = c.post(endpoint, headers={"Authorization": f"Bearer {settings[key_field]}"},
                                 data=data, files={"file": (part.name, fh, "audio/mpeg")})
                if res.status_code != 429 and res.status_code < 500:
                    break
                wait = min(float(res.headers.get("retry-after") or 2 ** (attempt + 1)), 120)
                tick(f"{label} is busy, waiting {int(wait)}s", i / len(parts))
                time.sleep(wait)
            if res.status_code == 401:
                raise Unavailable(f"{label} rejected the API key. Check it in Settings.")
            if res.status_code != 200:
                raise Unavailable(f"{label} returned an error: {api_error(res)}")
            body = res.json()
            language = language or body.get("language") or ""
            offset = i * 1200
            for s in body.get("segments") or []:
                segments.append({"start": s["start"] + offset, "end": s["end"] + offset, "text": s["text"]})
    return {"segments": segments, "language": language, "method": f"Whisper via {label}"}


def api_error(res: httpx.Response) -> str:
    try:
        err = res.json().get("error")
        message = err.get("message") if isinstance(err, dict) else err
    except ValueError:
        message = None
    return re.sub(r"\s+", " ", str(message or res.text or res.status_code))[:300]


def active_transcriber(settings: dict) -> str:
    choice = settings.get("transcriber") or "auto"
    if choice == "groq" and settings.get("groq_key"):
        return "groq"
    if choice == "openai" and settings.get("openai_key"):
        return "openai"
    if choice == "auto" and settings.get("groq_key"):
        return "groq"
    return "local"


def transcribe(path: Path, folder: Path, settings: dict, tick: Tick, speech_only: bool = True) -> dict:
    engine = active_transcriber(settings)
    if engine == "local":
        return transcribe_local(path, settings, tick, speech_only)
    return transcribe_cloud(path, folder, engine, settings, tick)
