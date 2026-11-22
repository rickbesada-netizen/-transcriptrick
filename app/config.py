"""Paths and saved settings. Everything lives in the local data folder."""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
ROOT = Path(sys._MEIPASS) if FROZEN else Path(__file__).resolve().parent.parent  # type: ignore[attr-defined]
# The installed app keeps its data in the user's profile, since Program folders can't be written to.
_default_data = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share") / "Transcriptrick" if FROZEN else ROOT / "data"
DATA = Path(os.environ.get("TRANSCRIPTRICK_DATA", _default_data))
DB_PATH = DATA / "transcriptrick.db"
SETTINGS_PATH = DATA / "settings.json"
MODELS_DIR = DATA / "models"
BIN_DIR = DATA / "bin"
TEMP_DIR = DATA / "temp"
LIB_DIR = DATA / "lib"
for _d in (DATA, MODELS_DIR, BIN_DIR, TEMP_DIR, LIB_DIR):
    _d.mkdir(parents=True, exist_ok=True)

SECRET_FIELDS = ("groq_key", "anthropic_key", "openai_key", "gemini_key", "openrouter_key")

DEFAULTS = {
    "transcriber": "auto",          # auto | local | groq | openai
    "whisper_model": "base",
    "language": "",                 # blank = detect
    "prefer_captions": True,
    "ai_provider": "groq",          # groq | anthropic | openai | gemini | openrouter | ollama
    "ai_model": "",
    "ollama_url": "http://localhost:11434/v1",
    "summary_language": "English",
    "auto_summary": True,
    "default_style": "overview",
    "collection_limit": 500,
    "cookies_browser": "",          # chrome | edge | firefox | brave
    "cookies_file": "",
    "proxy": "",
    "download_folder": str(Path.home() / "Downloads" / "Transcriptrick"),
    "download_format": "mp4",       # mp4 | mp3
    "download_quality": "1080",     # best | 2160 | 1440 | 1080 | 720 | 480
    **{k: "" for k in SECRET_FIELDS},
}

_lock = threading.Lock()


def load_settings() -> dict:
    with _lock:
        saved = {}
        if SETTINGS_PATH.exists():
            try:
                saved = json.loads(SETTINGS_PATH.read_text("utf-8"))
            except (OSError, ValueError):
                saved = {}
        return {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}


def save_settings(changes: dict) -> dict:
    current = load_settings()
    for key, value in changes.items():
        if key not in DEFAULTS:
            continue
        # A masked key coming back from the page means "leave it unchanged".
        if key in SECRET_FIELDS and isinstance(value, str) and value.startswith("••"):
            continue
        current[key] = value
    with _lock:
        tmp = SETTINGS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(current, indent=2), "utf-8")
        tmp.replace(SETTINGS_PATH)
    return current


def public_settings(settings: dict | None = None) -> dict:
    s = dict(settings or load_settings())
    for key in SECRET_FIELDS:
        value = s.get(key) or ""
        s[key] = ("••••" + value[-4:]) if value else ""
    return s


def ffmpeg_dir() -> str | None:
    """Folder holding a binary literally named ffmpeg, which yt-dlp insists on."""
    found = shutil.which("ffmpeg")
    if found:
        return str(Path(found).parent)
    name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    target = BIN_DIR / name
    if not target.exists():
        try:
            import imageio_ffmpeg

            shutil.copy2(imageio_ffmpeg.get_ffmpeg_exe(), target)
            target.chmod(0o755)
        except Exception:
            return None
    return str(BIN_DIR)


def deno_exe() -> str | None:
    """The JavaScript runtime yt-dlp needs for YouTube. The installed app carries its own copy."""
    if FROZEN:
        bundled = ROOT / "deno_bin" / ("deno.exe" if sys.platform == "win32" else "deno")
        return str(bundled) if bundled.exists() else None
    try:
        import deno

        found = deno.find_deno_bin()
        return found if Path(found).exists() else None
    except Exception:
        return shutil.which("deno")


def ffmpeg_exe() -> str | None:
    folder = ffmpeg_dir()
    if not folder:
        return None
    return str(Path(folder) / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"))
