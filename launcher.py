"""Transcriptrick desktop app: runs the local server and shows it in its own window."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

# A windowed app has no console, so stray prints must not crash it.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

from app.config import DATA  # noqa: E402
from app import updater  # noqa: E402

updater.activate()

HOST = "127.0.0.1"
LOCK = DATA / "running.json"


def alive(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/api/status", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def free_port(start: int = 8765) -> int:
    for port in range(start, start + 40):
        with socket.socket() as s:
            if s.connect_ex((HOST, port)) != 0:
                return port
    raise SystemExit("No free port.")


def edge() -> str | None:
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if base:
            path = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if path.exists():
                return str(path)
    return None


def open_window(url: str) -> subprocess.Popen | None:
    """An app-style Edge window with its own profile, so closing it means closing Transcriptrick."""
    exe = edge()
    if not exe:
        webbrowser.open(url)
        return None
    return subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={DATA / 'window'}", "--no-first-run",
                             "--no-default-browser-check", "--window-size=1320,880", "--disable-features=Translate"])


def serve(port: int):
    import uvicorn

    from app.main import app

    config = uvicorn.Config(app, host=HOST, port=port, log_level="warning", log_config=None)
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True, name="server").start()
    for _ in range(200):
        if server.started:
            return server
        time.sleep(0.05)
    raise RuntimeError("The server didn't start.")


def selftest(out: str) -> None:
    """Used by the build to check the packaged app works before it's released."""
    report: dict = {}
    try:
        port = free_port(8900)
        serve(port)
        with urllib.request.urlopen(f"http://{HOST}:{port}/api/status", timeout=10) as r:
            report["status"] = json.loads(r.read())
        with urllib.request.urlopen(f"http://{HOST}:{port}/", timeout=10) as r:
            report["page"] = r.status
        from app.config import ffmpeg_exe
        report["ffmpeg"] = bool(ffmpeg_exe()) and Path(ffmpeg_exe()).exists()
        from app.config import deno_exe
        report["deno"] = bool(deno_exe())
        import yt_dlp
        report["yt_dlp"] = yt_dlp.version.__version__
        import ctranslate2, faster_whisper  # noqa: E401,F401
        report["whisper"] = ctranslate2.__version__
        report["vad"] = any(Path(faster_whisper.__file__).parent.joinpath("assets").glob("*.onnx"))
        import av  # noqa: F401
        report["ok"] = all([report["page"] == 200, report["ffmpeg"], report["deno"], report["vad"]])
    except Exception as exc:
        report["ok"] = False
        report["error"] = f"{type(exc).__name__}: {exc}"
    Path(out).write_text(json.dumps(report, indent=2, default=str), "utf-8")
    os._exit(0 if report.get("ok") else 1)


def main() -> None:
    if len(sys.argv) > 2 and sys.argv[1] == "--selftest":
        selftest(sys.argv[2])
    # A second launch just brings up another window on the running copy.
    try:
        running = json.loads(LOCK.read_text("utf-8"))
        if alive(running["port"]):
            open_window(f"http://{HOST}:{running['port']}")
            return
    except (OSError, ValueError, KeyError):
        pass
    port = free_port()
    serve(port)
    LOCK.write_text(json.dumps({"port": port, "pid": os.getpid()}), "utf-8")
    updater.check_in_background()
    window = open_window(f"http://{HOST}:{port}")
    try:
        if window is None:
            while True:          # no Edge: keep serving the browser tab until the app is ended
                time.sleep(3600)
        window.wait()
        # Edge sometimes hands the window to a process it already had open; wait for that one too.
        while edge_window_open():
            time.sleep(2)
    finally:
        LOCK.unlink(missing_ok=True)
        os._exit(0)


def edge_window_open() -> bool:
    if sys.platform != "win32":
        return False
    marker = str(DATA / "window").lower()
    try:
        out = subprocess.run(["wmic", "process", "where", "name='msedge.exe'", "get", "CommandLine"],
                             capture_output=True, text=True, timeout=10, creationflags=0x08000000).stdout.lower()
    except Exception:
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  "Get-CimInstance Win32_Process -Filter \"name='msedge.exe'\" | Select -Expand CommandLine"],
                                 capture_output=True, text=True, timeout=20, creationflags=0x08000000).stdout.lower()
        except Exception:
            return False
    return marker in out


if __name__ == "__main__":
    main()
