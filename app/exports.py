"""Save transcripts and summaries as files."""
from __future__ import annotations

import io
import json
import re
import zipfile

from .timing import clock, paragraphs, plain_text

FORMATS = {
    "txt": "text/plain; charset=utf-8", "timed": "text/plain; charset=utf-8", "md": "text/markdown; charset=utf-8",
    "srt": "application/x-subrip; charset=utf-8", "vtt": "text/vtt; charset=utf-8",
    "json": "application/json; charset=utf-8",
}
EXTENSIONS = {"timed": "txt"}


def safe_name(title: str | None) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", title or "transcript")
    name = re.sub(r"\s+", " ", name).strip(" .")[:80]
    return name or "transcript"


def _stamp(seconds: float, sep: str) -> str:
    ms = round(seconds * 1000)
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def to_srt(segments) -> str:
    return "\n".join(f"{i}\n{_stamp(s['start'], ',')} --> {_stamp(s['end'], ',')}\n{s['text']}\n"
                     for i, s in enumerate(segments, 1))


def to_vtt(segments) -> str:
    return "WEBVTT\n\n" + "\n".join(f"{_stamp(s['start'], '.')} --> {_stamp(s['end'], '.')}\n{s['text']}\n"
                                    for s in segments)


def render(job: dict, summaries: list[dict], fmt: str) -> str:
    segs = job["segments"]
    lyric = "lyrics" in (job.get("method") or "").lower()
    if fmt == "txt":
        return plain_text(segs, lyric) + "\n"
    if fmt == "timed":
        return "\n\n".join(f"[{clock(p['start'])}] {p['text']}" for p in paragraphs(segs, lyric)) + "\n"
    if fmt == "srt":
        return to_srt(segs)
    if fmt == "vtt":
        return to_vtt(segs)
    done = [s for s in summaries if s["status"] == "done"]
    if fmt == "json":
        return json.dumps({"title": job["title"], "author": job.get("author"), "url": job["url"],
                           "source": job["source"], "duration": job.get("duration"), "language": job.get("language"),
                           "method": job.get("method"), "segments": segs,
                           "summaries": {s["style"]: s["content"] for s in done}}, ensure_ascii=False, indent=2)
    lines = [f"# {job['title']}", ""]
    if job.get("author"):
        lines += [f"By {job['author']}", ""]
    lines += [job["url"], ""]
    for s in done:
        lines += [s["content"], ""]
    lines += ["## Transcript", ""]
    lines += [f"**[{clock(p['start'])}]** {p['text']}\n" for p in paragraphs(segs, lyric)]
    return "\n".join(lines)


def batch_zip(batch: dict, summaries_of, collection_summary: str | None) -> bytes:
    folder = safe_name(batch["title"])
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        if collection_summary:
            zf.writestr(f"{folder}/Collection summary.md", collection_summary)
        used: set[str] = set()
        for i, job in enumerate(j for j in batch["jobs"] if j["status"] == "done"):
            name = f"{i + 1:03d} {safe_name(job['title'])}"
            used.add(name)
            zf.writestr(f"{folder}/{name}.md", render(job, summaries_of(job["id"]), "md"))
    return buffer.getvalue()
