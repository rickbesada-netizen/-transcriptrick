"""Keep the bundled downloader current. YouTube changes often, and yt-dlp releases fixes every few days."""
from __future__ import annotations

import json
import re
import sys
import threading
from pathlib import Path

from .config import FROZEN, LIB_DIR

PACKAGES = ("yt-dlp", "youtube-transcript-api")
STATE = LIB_DIR / "versions.json"


def _key(version: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", version))


def _bundled(name: str) -> str:
    try:
        from importlib.metadata import version

        return version(name)
    except Exception:
        return "0"


def _state() -> dict:
    try:
        return json.loads(STATE.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def activate() -> None:
    """Put any newer downloaded wheels ahead of the bundled copies. Call before importing them."""
    for name, entry in _state().items():
        wheel = LIB_DIR / entry.get("file", "")
        if wheel.is_file() and _key(entry.get("version", "0")) > _key(_bundled(name)):
            sys.path.insert(0, str(wheel))


def check() -> None:
    """Fetch newer pure-Python wheels for the next start. Failures are ignored; the bundled copy still works."""
    import httpx

    state = _state()
    for name in PACKAGES:
        try:
            data = httpx.get(f"https://pypi.org/pypi/{name}/json", timeout=20).json()
            latest = data["info"]["version"]
            current = state.get(name, {}).get("version") or _bundled(name)
            if _key(latest) <= _key(current):
                continue
            wheel = next(u for u in data["urls"] if u["packagetype"] == "bdist_wheel" and u["filename"].endswith("none-any.whl"))
            target = LIB_DIR / wheel["filename"]
            tmp = target.with_suffix(".part")
            with httpx.stream("GET", wheel["url"], timeout=120, follow_redirects=True) as res:
                res.raise_for_status()
                with open(tmp, "wb") as fh:
                    for chunk in res.iter_bytes(1 << 16):
                        fh.write(chunk)
            tmp.replace(target)
            old = state.get(name, {}).get("file")
            state[name] = {"version": latest, "file": wheel["filename"]}
            STATE.write_text(json.dumps(state, indent=2), "utf-8")
            if old and old != wheel["filename"]:
                (LIB_DIR / old).unlink(missing_ok=True)
        except Exception:
            continue


def check_in_background() -> None:
    if FROZEN:
        threading.Thread(target=check, daemon=True, name="updater").start()
