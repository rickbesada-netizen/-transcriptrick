"""Save videos as MP4 or audio as MP3 from YouTube and the other sites yt-dlp supports."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import db
from .config import load_settings
from .exports import safe_name
from .sources import LinkError, friendly_ydl_error, matches, host_of, resolve_ytdlp, source_of, SPOTIFY, ydl_options

QUALITIES = ("best", "2160", "1440", "1080", "720", "480", "360")
_wake = threading.Event()


class Stopped(Exception):
    pass


def resolve_any(url: str, settings: dict) -> dict:
    """List what a link holds, for any site yt-dlp understands."""
    if matches(host_of(url), SPOTIFY):
        raise LinkError("Spotify's audio is copy-protected, so it can't be downloaded. Use the Transcribe button for "
                        "Spotify links instead.")
    source = source_of(url) or "web"
    result = resolve_ytdlp(url, source, settings)
    if source == "web":
        host = host_of(url).removeprefix("www.")
        result["source"] = "Web" if re.fullmatch(r"[\d.]+|localhost", host) else host.split(".")[0].capitalize()
    return result


def format_options(fmt: str, quality: str) -> dict:
    if fmt == "mp3":
        return {"format": "bestaudio/best",
                "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"},
                                   {"key": "FFmpegMetadata"}]}
    height = "" if quality == "best" else quality
    sort = [f"res:{height}" if height else "res", "vcodec:h264", "acodec:m4a", "ext:mp4:m4a"]
    return {"format": "bv*+ba/b", "format_sort": sort, "merge_output_format": "mp4",
            # Some sites hand over formats an MP4 can't hold; convert those rather than leave a .webm.
            "postprocessors": [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}]}


def target_folder(item: dict, settings: dict) -> Path:
    base = Path(os.path.expandvars(os.path.expanduser(settings.get("download_folder") or "~/Downloads/Transcriptrick")))
    if item.get("group_title"):
        base = base / safe_name(item["group_title"])
    base.mkdir(parents=True, exist_ok=True)
    return base


def run_download(item: dict) -> None:
    import yt_dlp

    did = item["id"]
    settings = load_settings()
    db.update_download(did, status="working", stage="Starting", progress=0, error=None)
    last = [0.0]

    def hook(d):
        if db.download_status(did) != "working":
            raise Stopped()
        now = time.time()
        if d.get("status") == "downloading" and now - last[0] > 0.5:
            last[0] = now
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            share = d.get("downloaded_bytes", 0) / total if total else None
            what = "video" if d.get("info_dict", {}).get("vcodec") not in (None, "none") else "audio"
            speed = d.get("speed")
            note = f"Downloading {what}" + (f", {speed / 1048576:.1f} MB/s" if speed else "")
            db.update_download(did, stage=note, **({"progress": round(min(share, 1), 3)} if share is not None else {}))
        elif d.get("status") == "finished":
            db.update_download(did, stage="Finishing the file", progress=1)

    def post_hook(d):
        if d.get("status") == "started":
            db.update_download(did, stage="Converting" if item["format"] == "mp3" else "Joining video and audio")

    folder = target_folder(item, settings)
    prefix = f"{item['position'] + 1:03d} " if item.get("group_title") else ""
    opts = ydl_options(settings, noplaylist=True, ignore_no_formats_error=False, overwrites=False,
                       outtmpl=str(folder / f"{prefix}%(title).150B.%(ext)s"), windowsfilenames=True,
                       progress_hooks=[hook], postprocessor_hooks=[post_hook], concurrent_fragment_downloads=4,
                       **format_options(item["format"], item["quality"]))
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(item["url"], download=True)
        files = [r.get("filepath") for r in (info or {}).get("requested_downloads") or [] if r.get("filepath")]
        path = Path(files[-1]) if files else None
        if item["format"] == "mp3" and path and path.suffix != ".mp3":
            path = path.with_suffix(".mp3")
        if not path or not path.exists():
            raise RuntimeError("The file wasn't saved.")
        if db.download_status(did) != "working":
            return
        db.update_download(did, status="done", stage="Saved", progress=1, file=str(path), size=path.stat().st_size,
                           title=(info or {}).get("title") or item["title"], finished=time.time())
    except Exception as exc:
        # The progress hook stops a cancelled download by raising, and yt-dlp may wrap that error.
        if db.download_status(did) != "working":
            cleanup_parts(folder)
            return
        db.update_download(did, status="failed", stage="Failed", error=friendly_ydl_error(exc), finished=time.time())


def cleanup_parts(folder: Path) -> None:
    for p in folder.glob("*.part"):
        try:
            p.unlink()
        except OSError:
            pass


def loop() -> None:
    while True:
        item = db.next_download()
        if item:
            try:
                run_download(item)
            except Exception as exc:   # keep the queue alive whatever happens
                db.update_download(item["id"], status="failed", stage="Failed", error=str(exc)[:300])
        else:
            _wake.wait(2)
            _wake.clear()


def wake() -> None:
    _wake.set()


def start() -> None:
    threading.Thread(target=loop, daemon=True, name="downloader").start()


def reveal(path: str | None, folder_only: bool = False) -> None:
    """Show a file in Explorer (or Finder), or open its folder."""
    if not path:
        raise FileNotFoundError("Nothing to show.")
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError("That file was moved or deleted.")
    if sys.platform == "win32":
        if folder_only or target.is_dir():
            os.startfile(str(target if target.is_dir() else target.parent))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["explorer", "/select,", str(target)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(target)] if not folder_only else ["open", str(target.parent)])
    else:
        subprocess.Popen(["xdg-open", str(target if target.is_dir() else target.parent)])


def open_file(path: str | None) -> None:
    if not path or not Path(path).exists():
        raise FileNotFoundError("That file was moved or deleted.")
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])
