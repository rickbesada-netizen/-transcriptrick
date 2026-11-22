"""Work out what a pasted link is and list everything inside it."""
from __future__ import annotations

import json
import re
import urllib.parse as up

import httpx

from .config import deno_exe, ffmpeg_dir

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
URL_RE = re.compile(r"https?://[^\s<>\"']+|(?:www\.|m\.|music\.|open\.|on\.)?"
                    r"(?:youtube\.com|youtu\.be|soundcloud\.com|spotify\.com|spotify\.link)/[^\s<>\"']+", re.I)

YOUTUBE = ("youtube.com", "youtu.be", "youtube-nocookie.com")
SOUNDCLOUD = ("soundcloud.com", "snd.sc")
SPOTIFY = ("spotify.com", "spotify.link", "spotify.app.link")


class LinkError(Exception):
    """A link that cannot be used, with a message written for the person using the app."""


def find_links(text: str) -> list[str]:
    seen, links = set(), []
    for raw in URL_RE.findall(text or ""):
        url = raw.rstrip(".,;)]}")
        if not url.lower().startswith("http"):
            url = "https://" + url
        if url not in seen:
            seen.add(url)
            links.append(url)
    return links


def host_of(url: str) -> str:
    return (up.urlsplit(url).hostname or "").lower()


def matches(host: str, domains) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def source_of(url: str) -> str | None:
    host = host_of(url)
    if matches(host, YOUTUBE):
        return "youtube"
    if matches(host, SOUNDCLOUD):
        return "soundcloud"
    if matches(host, SPOTIFY):
        return "spotify"
    return None


def client(settings: dict, timeout: float = 30) -> httpx.Client:
    return httpx.Client(headers={"User-Agent": UA}, follow_redirects=True, timeout=timeout,
                        proxy=(settings.get("proxy") or None))


def ydl_options(settings: dict, **extra) -> dict:
    opts = {"quiet": True, "no_warnings": True, "noprogress": True, "socket_timeout": 30, "retries": 3,
            "ignore_no_formats_error": True, "logger": _Quiet(), "cachedir": False}
    runtimes = {"node": {}}
    deno_path = deno_exe()
    if deno_path:
        runtimes = {"deno": {"path": deno_path}, "node": {}}
    opts["js_runtimes"] = runtimes
    # Lets yt-dlp fetch a newer YouTube challenge solver if the bundled one falls behind.
    opts["remote_components"] = {"ejs:github"}
    location = ffmpeg_dir()
    if location:
        opts["ffmpeg_location"] = location
    if settings.get("proxy"):
        opts["proxy"] = settings["proxy"]
    if settings.get("cookies_file"):
        opts["cookiefile"] = settings["cookies_file"]
    elif settings.get("cookies_browser"):
        opts["cookiesfrombrowser"] = (settings["cookies_browser"],)
    opts.update(extra)
    return opts


class _Quiet:
    def debug(self, _):
        pass

    def warning(self, _):
        pass

    def error(self, _):
        pass


def friendly_ydl_error(exc: Exception) -> str:
    text = re.sub(r"\x1b\[[0-9;]*m", "", str(exc)).replace("ERROR: ", "").strip()
    low = text.lower()
    if any(w in low for w in ("unable to download", "connection", "timed out", "name resolution", "tunnel", "proxy")) \
            and not re.search(r"sign in|private|unavailable|\bage\b", low):
        return "The site couldn't be reached. Check your connection or proxy, then try again."
    if re.search(r"\bage[- ]restrict|confirm your age|inappropriate for some users", low):
        return "This item is age restricted. In Settings, choose your browser under Use my browser sign-in, then retry."
    if "sign in to confirm" in low or "not a bot" in low:
        return ("YouTube wants a signed-in browser for this request. In Settings, choose your browser under "
                "Use my browser sign-in, or add a proxy, then retry.")
    if "private" in low:
        return "This item is private, so it can't be read."
    if "unavailable" in low or "removed" in low or "not available" in low:
        return "This item is unavailable. It may be removed or blocked in your country."
    if "members" in low or "premium" in low:
        return "This item is for paying members only."
    return text[:400] or "The site refused the request."


# ---------- YouTube and SoundCloud ----------

def clean_youtube_url(url: str) -> str:
    parts = up.urlsplit(url)
    query = up.parse_qs(parts.query)
    path = parts.path.rstrip("/")
    host = host_of(url)
    if host == "youtu.be":
        vid = path.strip("/").split("/")[0]
        lst = query.get("list", [""])[0]
        return f"https://www.youtube.com/watch?v={vid}" + (f"&list={lst}" if _real_list(lst) else "")
    lst = query.get("list", [""])[0]
    if path == "/watch":
        vid = query.get("v", [""])[0]
        if _real_list(lst):
            return f"https://www.youtube.com/playlist?list={lst}"
        return f"https://www.youtube.com/watch?v={vid}"
    # A channel link should mean its uploads rather than a list of tabs.
    if re.fullmatch(r"/(@[^/]+|channel/[^/]+|c/[^/]+|user/[^/]+)", path):
        return f"https://www.youtube.com{path}/videos"
    return url


def _real_list(lst: str) -> bool:
    """Auto-generated mixes (RD...) never end, so a video opened from one counts as that video alone."""
    return bool(lst) and not lst.upper().startswith(("RD", "UL"))


def _thumb(entry: dict) -> str | None:
    if entry.get("thumbnail"):
        return entry["thumbnail"]
    thumbs = entry.get("thumbnails") or []
    return thumbs[-1].get("url") if thumbs else None


def _flatten(info: dict, limit: int, out: list) -> None:
    for entry in info.get("entries") or []:
        if len(out) >= limit:
            return
        if not entry:
            continue
        if entry.get("_type") == "playlist" or entry.get("entries"):
            _flatten(entry, limit, out)
        else:
            out.append(entry)


def _entry_item(entry: dict, source: str, fallback_author=None) -> dict | None:
    url = entry.get("webpage_url") or entry.get("url")
    if source == "youtube" and entry.get("id") and not str(url or "").startswith("http"):
        url = f"https://www.youtube.com/watch?v={entry['id']}"
    if not url:
        return None
    return {"url": url, "source": source, "kind": "video" if source == "youtube" else "track",
            "title": entry.get("title") or "Untitled",
            "author": entry.get("uploader") or entry.get("channel") or fallback_author,
            "duration": entry.get("duration"), "thumbnail": _thumb(entry),
            "meta": {"id": entry.get("id")}}


def resolve_ytdlp(url: str, source: str, settings: dict) -> dict:
    import yt_dlp

    if source == "youtube":
        url = clean_youtube_url(url)
    limit = int(settings.get("collection_limit") or 500)
    opts = ydl_options(settings, extract_flat="in_playlist", skip_download=True, playlistend=limit,
                       noplaylist=False, lazy_playlist=False)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        raise LinkError(friendly_ydl_error(exc)) from exc
    if not info:
        raise LinkError("Nothing was found at that link.")
    if info.get("_type") in ("playlist", "multi_video") or info.get("entries") is not None:
        raw: list = []
        _flatten(info, limit, raw)
        author = info.get("uploader") or info.get("channel")
        items = [i for i in (_entry_item(e, source, author) for e in raw) if i]
        items = [i for i in items if i["title"] not in ("[Private video]", "[Deleted video]")]
        if not items:
            raise LinkError("That playlist is empty or private.")
        return {"title": info.get("title") or "Playlist", "url": url, "source": source,
                "is_collection": True, "items": items}
    item = _entry_item(info, source)
    if not item:
        raise LinkError("Nothing playable was found at that link.")
    return {"title": item["title"], "url": url, "source": source, "is_collection": False, "items": [item]}


# ---------- Spotify ----------

def parse_spotify(url: str) -> tuple[str, str] | None:
    m = re.search(r"/(track|album|playlist|episode|show)/([A-Za-z0-9]{10,})", up.urlsplit(url).path)
    return (m.group(1), m.group(2)) if m else None


def next_data(page: str) -> dict | None:
    m = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except ValueError:
        return None


def find_entity(data) -> dict | None:
    """Spotify moves this around between releases, so accept the usual path or search for it."""
    try:
        entity = data["props"]["pageProps"]["state"]["data"]["entity"]
        if isinstance(entity, dict):
            return entity
    except (KeyError, TypeError):
        pass
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if isinstance(node.get("uri"), str) and node["uri"].startswith("spotify:") and (
                    "title" in node or "name" in node):
                return node
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return None


def _cover(entity: dict) -> str | None:
    images = (entity.get("visualIdentity") or {}).get("image") or (entity.get("coverArt") or {}).get("sources") or []
    if images:
        return max(images, key=lambda i: i.get("maxWidth") or i.get("width") or 0).get("url")
    return None


def spotify_entity(kind: str, sid: str, settings: dict) -> dict:
    with client(settings) as c:
        res = c.get(f"https://open.spotify.com/embed/{kind}/{sid}")
        if res.status_code == 404:
            raise LinkError("Spotify says this item doesn't exist.")
        entity = find_entity(next_data(res.text) or {})
        if entity:
            return entity
        canonical = f"https://open.spotify.com/{kind}/{sid}"
        oe = c.get("https://open.spotify.com/oembed", params={"url": canonical})
    if oe.status_code == 200:
        data = oe.json()
        return {"title": data.get("title"), "thumb": data.get("thumbnail_url"), "trackList": []}
    raise LinkError("Spotify didn't return details for this link. Try again shortly.")


def spotify_item(kind: str, sid: str, title, author, duration_ms, cover) -> dict:
    meta: dict = {"spotify_type": kind, "id": sid}
    if kind == "track":
        meta["artist"], meta["track"] = author, title
    else:
        meta["show"] = author
    return {"url": f"https://open.spotify.com/{kind}/{sid}", "source": "spotify",
            "kind": "podcast" if kind == "episode" else "track", "title": title or "Untitled", "author": author,
            "duration": (duration_ms / 1000) if duration_ms else None, "thumbnail": cover, "meta": meta}


def resolve_spotify(url: str, settings: dict) -> dict:
    if host_of(url) != "open.spotify.com":          # share short links
        with client(settings) as c:
            url = str(c.get(url).url)
    parsed = parse_spotify(url)
    if not parsed:
        raise LinkError("Use a Spotify track, album, playlist, show or episode link.")
    kind, sid = parsed
    entity = spotify_entity(kind, sid, settings)
    title = entity.get("title") or entity.get("name") or "Spotify"
    cover = _cover(entity) or entity.get("thumb")
    canonical = f"https://open.spotify.com/{kind}/{sid}"
    if kind in ("track", "episode"):
        author = entity.get("subtitle")
        if not author and entity.get("artists"):
            author = ", ".join(a.get("name", "") for a in entity["artists"])
        item = spotify_item(kind, sid, title, author, entity.get("duration"), cover)
        return {"title": title, "url": canonical, "source": "spotify", "is_collection": False, "items": [item]}
    items = []
    for t in entity.get("trackList") or []:
        uri = str(t.get("uri") or "").split(":")
        if len(uri) != 3 or uri[1] not in ("track", "episode"):
            continue
        items.append(spotify_item(uri[1], uri[2], t.get("title"), t.get("subtitle"), t.get("duration"), cover))
    items = items[: int(settings.get("collection_limit") or 500)]
    if not items:
        raise LinkError("Spotify didn't list any tracks for this link. It may be private.")
    return {"title": title, "url": canonical, "source": "spotify", "is_collection": True, "items": items}


def resolve(url: str, settings: dict) -> dict:
    source = source_of(url)
    if not source:
        raise LinkError("Only YouTube, Spotify and SoundCloud links work here.")
    if source == "spotify":
        return resolve_spotify(url, settings)
    return resolve_ytdlp(url, source, settings)
