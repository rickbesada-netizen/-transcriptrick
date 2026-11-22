"""Existing transcripts: YouTube captions and published song lyrics."""
from __future__ import annotations

import re

from .sources import client, ydl_options
from .timing import parse_lrc


def youtube_id(url: str) -> str | None:
    m = re.search(r"(?:v=|youtu\.be/|/shorts/|/live/|/embed/)([\w-]{11})", url)
    return m.group(1) if m else None


def choose_transcript(transcripts, wanted: str):
    items = list(transcripts)
    if not items:
        return None
    if wanted:
        for t in items:
            if t.language_code.split("-")[0] == wanted:
                return t
    generated = next((t for t in items if t.is_generated), None)
    if generated:   # the auto captions reveal the language actually spoken
        base = generated.language_code.split("-")[0]
        return next((t for t in items if not t.is_generated and t.language_code.split("-")[0] == base), generated)
    return items[0]


def _via_transcript_api(video_id: str, settings: dict) -> dict | None:
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api.proxies import GenericProxyConfig

    proxy = settings.get("proxy")
    api = YouTubeTranscriptApi(proxy_config=GenericProxyConfig(http_url=proxy, https_url=proxy) if proxy else None)
    chosen = choose_transcript(api.list(video_id), settings.get("language") or "")
    if not chosen:
        return None
    fetched = chosen.fetch()
    segs = [{"start": s.start, "end": s.start + s.duration, "text": s.text} for s in fetched.snippets]
    label = "YouTube auto captions" if chosen.is_generated else "YouTube captions"
    return {"segments": segs, "language": chosen.language_code, "method": label}


def parse_json3(data: dict) -> list[dict]:
    segs = []
    for event in data.get("events") or []:
        text = "".join(p.get("utf8", "") for p in event.get("segs") or []).strip()
        if text:
            start = event.get("tStartMs", 0) / 1000
            segs.append({"start": start, "end": start + event.get("dDurationMs", 0) / 1000, "text": text})
    return segs


def pick_track(info: dict, wanted: str):
    """Return (language, url, is_auto) for the best json3 caption track in yt-dlp's info."""
    def scan(group, auto):
        for lang, formats in (group or {}).items():
            url = next((f.get("url") for f in formats if f.get("ext") == "json3"), None)
            if url:
                yield lang, url, auto

    tracks = list(scan(info.get("subtitles"), False)) + list(scan(info.get("automatic_captions"), True))
    if not tracks:
        return None
    for code in filter(None, [wanted, (info.get("language") or "").split("-")[0], "en"]):
        hit = next((t for t in tracks if t[0].split("-")[0] == code), None)
        if hit:
            return hit
    return tracks[0]


def _via_ytdlp(url: str, settings: dict) -> dict | None:
    import yt_dlp

    with yt_dlp.YoutubeDL(ydl_options(settings, skip_download=True, noplaylist=True)) as ydl:
        info = ydl.extract_info(url, download=False)
    picked = pick_track(info or {}, settings.get("language") or "")
    if not picked:
        return None
    lang, track_url, auto = picked
    with client(settings) as c:
        res = c.get(track_url)
        res.raise_for_status()
    segs = parse_json3(res.json())
    if not segs:
        return None
    return {"segments": segs, "language": lang, "method": "YouTube auto captions" if auto else "YouTube captions"}


def youtube_captions(url: str, settings: dict) -> dict | None:
    """Captions if the video has any, or None. Failure here only means falling back to audio."""
    video_id = youtube_id(url)
    if video_id:
        try:
            found = _via_transcript_api(video_id, settings)
            if found and found["segments"]:
                return found
        except Exception:
            pass
    try:
        return _via_ytdlp(url, settings)
    except Exception:
        return None


def lyrics(artist: str, track: str, duration: float | None, settings: dict) -> dict | None:
    """Look a song up on LRCLIB, a free lyrics database."""
    artist = (artist or "").split(",")[0].strip()
    title = re.sub(r"\s*[-(\[].*?(remaster|version|edit|mix|live).*$", "", track or "", flags=re.I).strip()
    if not title:
        return None
    try:
        with client(settings, 20) as c:
            res = c.get("https://lrclib.net/api/search", params={"track_name": title, "artist_name": artist})
            results = res.json() if res.status_code == 200 else []
    except Exception:
        return None
    usable = [r for r in results if not r.get("instrumental") and (r.get("syncedLyrics") or r.get("plainLyrics"))]
    if not usable:
        return None

    def rank(r):
        off = abs((r.get("duration") or 0) - duration) if duration else 0
        return (not r.get("syncedLyrics"), off)

    best = min(usable, key=rank)
    if duration and best.get("duration") and abs(best["duration"] - duration) > 15:
        return None     # probably a different recording
    if best.get("syncedLyrics"):
        return {"segments": parse_lrc(best["syncedLyrics"]), "language": "", "method": "Published lyrics, LRCLIB"}
    lines = [ln.strip() for ln in best["plainLyrics"].splitlines() if ln.strip()]
    step = (duration or len(lines) * 3) / max(len(lines), 1)
    segs = [{"start": i * step, "end": (i + 1) * step, "text": ln} for i, ln in enumerate(lines)]
    return {"segments": segs, "language": "", "method": "Published lyrics, LRCLIB (unsynced)"}
