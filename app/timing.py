"""Helpers for timestamps and for grouping raw caption lines into readable paragraphs."""
from __future__ import annotations

import html
import re


def clock(seconds: float | None) -> str:
    s = int(seconds or 0)
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


_NOISE = re.compile(r"^\s*[\[(♪♫]*\s*(music|applause|laughter|música|musique|musik)?\s*[\])♪♫]*\s*$", re.I)


def clean_segments(raw) -> list[dict]:
    """Normalise (start, end, text) segments, dropping empties and stacked repeats."""
    out: list[dict] = []
    for seg in raw:
        text = re.sub(r"\s+", " ", html.unescape(str(seg.get("text") or ""))).strip()
        if not text or _NOISE.match(text):
            continue
        start = float(seg.get("start") or 0)
        end = float(seg.get("end") or start)
        if out and out[-1]["text"] == text and start - out[-1]["end"] < 3:
            out[-1]["end"] = max(out[-1]["end"], end)
            continue
        out.append({"start": round(start, 2), "end": round(max(end, start), 2), "text": text})
    return out


def paragraphs(segments: list[dict], line_by_line: bool = False) -> list[dict]:
    if line_by_line:
        return [{"start": s["start"], "text": s["text"]} for s in segments]
    groups: list[dict] = []
    current: list[str] = []
    began = last_end = 0.0
    for seg in segments:
        if current:
            span = seg["start"] - began
            pause = seg["start"] - last_end
            sentence_done = current[-1].rstrip()[-1:] in ".?!…"
            if span >= 75 or pause >= 4 or (span >= 35 and sentence_done):
                groups.append({"start": began, "text": " ".join(current)})
                current = []
        if not current:
            began = seg["start"]
        current.append(seg["text"])
        last_end = seg["end"]
    if current:
        groups.append({"start": began, "text": " ".join(current)})
    return groups


def plain_text(segments: list[dict], line_by_line: bool = False) -> str:
    joiner = "\n" if line_by_line else "\n\n"
    return joiner.join(p["text"] for p in paragraphs(segments, line_by_line))


def parse_lrc(lrc: str) -> list[dict]:
    """Turn synced LRC lyrics into segments."""
    stamped = []
    for line in lrc.splitlines():
        stamps = re.findall(r"\[(\d+):(\d+(?:\.\d+)?)\]", line)
        words = re.sub(r"\[[^\]]*\]", "", line).strip()
        if words:
            stamped += [(int(m) * 60 + float(s), words) for m, s in stamps]
    stamped.sort()
    return [{"start": t, "end": stamped[i + 1][0] if i + 1 < len(stamped) else t + 4, "text": w}
            for i, (t, w) in enumerate(stamped)]
