"""Downloads against a local test server, so no outside sites are needed."""
import functools
import http.server
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

os.environ["TRANSCRIPTRICK_DATA"] = tempfile.mkdtemp()

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import config, db, downloads, main  # noqa: E402

OUT = Path(tempfile.mkdtemp())


class Slow(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def copyfile(self, src, dst):
        if "slow" in self.path:
            while chunk := src.read(4096):
                dst.write(chunk)
                time.sleep(0.05)
        else:
            super().copyfile(src, dst)


@pytest.fixture(scope="module")
def site():
    folder = Path(tempfile.mkdtemp())
    ff = config.ffmpeg_exe()
    subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=3",
                    "-f", "lavfi", "-i", "sine=440:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(folder / "clip.mp4")], check=True)
    big = folder / "slow.mp4"
    subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25:duration=20",
                    "-c:v", "libx264", "-b:v", "3M", str(big)], check=True)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Slow, directory=str(folder)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture(scope="module")
def client(site):
    config.save_settings({"download_folder": str(OUT)})
    with TestClient(main.app, base_url="http://127.0.0.1") as c:
        yield c


def wait_for(did, states=("done", "failed", "cancelled"), seconds=60):
    end = time.time() + seconds
    while time.time() < end:
        d = db.get_download(did)
        if d["status"] in states:
            return d
        time.sleep(0.1)
    raise AssertionError(db.get_download(did))


def test_resolve_any_site_and_spotify_refused(client, site):
    res = client.post("/api/resolve", json={"text": f"{site}/clip.mp4", "mode": "download"}).json()
    assert res["collections"][0]["items"][0]["url"].endswith("clip.mp4")
    res = client.post("/api/resolve", json={"text": "https://open.spotify.com/track/abc", "mode": "download"}).json()
    assert "copy-protected" in res["errors"][0]["error"]


def test_mp4_and_mp3_into_playlist_folder(client, site):
    item = {"url": f"{site}/clip.mp4", "source": "web", "title": "Clip"}
    ids = client.post("/api/downloads", json={"format": "mp4", "quality": "720", "collections": [
        {"title": "My: List?", "is_collection": True, "items": [item, dict(item, title="Clip again")]}]}).json()["ids"]
    first = wait_for(ids[0])
    assert first["status"] == "done", first
    path = Path(first["file"])
    assert path.suffix == ".mp4" and path.parent.name == "My List" and path.name.startswith("001 ")
    assert wait_for(ids[1])["status"] == "done"
    mp3 = client.post("/api/downloads", json={"format": "mp3", "collections": [{"title": "x", "items": [item]}]}).json()["ids"][0]
    got = wait_for(mp3)
    assert got["status"] == "done" and got["file"].endswith(".mp3") and Path(got["file"]).parent == OUT
    assert config.load_settings()["download_format"] == "mp3"
    listed = client.get("/api/downloads").json()
    assert len(listed["downloads"]) == 3


def test_cancel_and_failure_keep_queue_going(client, site):
    slow = client.post("/api/downloads", json={"collections": [{"items": [{"url": f"{site}/slow.mp4", "title": "Slow"}]}]}).json()["ids"][0]
    bad = client.post("/api/downloads", json={"collections": [{"items": [{"url": f"{site}/missing.mp4", "title": "Gone"}]}]}).json()["ids"][0]
    wait_for(slow, ("working",))
    time.sleep(0.6)
    client.post(f"/api/downloads/{slow}/cancel")
    assert wait_for(slow)["status"] == "cancelled"
    failed = wait_for(bad)
    assert failed["status"] == "failed" and failed["error"]
    assert not list(OUT.glob("*.part"))
    client.post("/api/downloads/clear")
    assert client.get("/api/downloads").json()["downloads"] == []


def test_reveal_missing_file_is_clear(client):
    did = db.add_downloads(None, False, [{"url": "http://x", "title": "t"}], "mp4", "720")[0]
    db.update_download(did, status="done", file=str(OUT / "nope.mp4"))
    res = client.post(f"/api/downloads/{did}/reveal")
    assert res.status_code == 410 and "moved or deleted" in res.json()["detail"]


def test_format_options():
    assert downloads.format_options("mp4", "1080")["format_sort"][0] == "res:1080"
    assert downloads.format_options("mp4", "best")["format_sort"][0] == "res"
    assert downloads.format_options("mp3", "best")["postprocessors"][0]["preferredcodec"] == "mp3"
