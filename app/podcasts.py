"""Find a Spotify podcast episode's public audio through Apple's directory and the show's RSS feed."""
from __future__ import annotations

import difflib
import re
import xml.etree.ElementTree as ET

from .sources import client


def normalise(text: str) -> str:
    text = re.sub(r"^\s*(ep(isode)?\.?\s*)?#?\d+\s*[:.\-–|]\s*", "", (text or "").lower())
    return re.sub(r"[^\w ]+", " ", text).strip()


def similarity(a: str, b: str) -> float:
    a, b = normalise(a), normalise(b)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 0.95
    return difflib.SequenceMatcher(None, a, b).ratio()


def best_rss_match(feed_xml: bytes | str, title: str) -> str | None:
    try:
        root = ET.fromstring(feed_xml)
    except ET.ParseError:
        return None
    best, score = None, 0.0
    for item in root.iter("item"):
        enclosure = item.find("enclosure")
        name = item.findtext("title") or ""
        if enclosure is None or not enclosure.get("url"):
            continue
        s = similarity(name, title)
        if s > score:
            best, score = enclosure.get("url"), s
    return best if score >= 0.72 else None


def find_episode_audio(title: str, show: str, settings: dict) -> str | None:
    with client(settings, 30) as c:
        # Apple's episode search often answers directly.
        res = c.get("https://itunes.apple.com/search",
                    params={"media": "podcast", "entity": "podcastEpisode", "term": f"{show} {title}"[:200],
                            "limit": 25})
        if res.status_code == 200:
            for r in res.json().get("results", []):
                same_show = not show or similarity(r.get("collectionName", ""), show) >= 0.7
                if same_show and r.get("episodeUrl") and similarity(r.get("trackName", ""), title) >= 0.72:
                    return r["episodeUrl"]
        if not show:
            return None
        # Otherwise walk the show's own feed.
        res = c.get("https://itunes.apple.com/search",
                    params={"media": "podcast", "entity": "podcast", "term": show[:200], "limit": 8})
        if res.status_code != 200:
            return None
        for r in res.json().get("results", []):
            if similarity(r.get("collectionName", ""), show) < 0.7 or not r.get("feedUrl"):
                continue
            try:
                feed = c.get(r["feedUrl"])
                found = best_rss_match(feed.content, title) if feed.status_code == 200 else None
            except Exception:
                found = None
            if found:
                return found
    return None
