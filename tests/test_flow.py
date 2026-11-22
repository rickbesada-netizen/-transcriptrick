"""End-to-end flows against stand-in services, so no accounts or network are needed."""
import http.server
import json
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

os.environ["TRANSCRIPTRICK_DATA"] = tempfile.mkdtemp()

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import audio, captions, config, db, main, podcasts, sources, summarize, worker  # noqa: E402

received = {"chat": [], "uploads": []}


class Upstream(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = Path(self.server.tone).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        data = self.rfile.read(int(self.headers["Content-Length"]))
        if "audio/transcriptions" in self.path:
            received["uploads"].append(len(data))
            out = {"language": "en", "segments": [{"start": 1, "end": 4, "text": " Spoken words here."}]}
        elif "messages" in self.path:
            received["chat"].append(json.loads(data))
            out = {"content": [{"type": "text", "text": "## In short\nFrom Claude [0:01]."}]}
        else:
            received["chat"].append(json.loads(data))
            out = {"choices": [{"message": {"content": "## In short\nA test summary [0:01]."}}]}
        raw = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture(scope="module")
def upstream():
    folder = tempfile.mkdtemp()
    tone = Path(folder) / "tone.mp3"
    subprocess.run([config.ffmpeg_exe(), "-y", "-v", "error", "-f", "lavfi", "-i", "sine=440:duration=3", str(tone)],
                   check=True)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    server.tone = str(tone)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture(scope="module")
def app_client(upstream):
    config.save_settings({"ai_provider": "ollama", "ollama_url": upstream + "/v1", "auto_summary": True})
    with TestClient(main.app, base_url="http://127.0.0.1") as client:
        yield client


def wait(check, seconds=15):
    end = time.time() + seconds
    while time.time() < end:
        value = check()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("timed out")


def job_item(url="https://www.youtube.com/watch?v=abcdefghijk", **kw):
    return {"url": url, "source": "youtube", "kind": "video", "title": "Talk", "author": "Host", "duration": 60, **kw}


def test_caption_job_to_summary_and_exports(app_client, monkeypatch):
    monkeypatch.setattr(captions, "youtube_captions", lambda u, s: {
        "segments": [{"start": 1, "end": 3, "text": "Hello there."}, {"start": 3, "end": 5, "text": "Ignore this [Music]"}],
        "language": "en", "method": "YouTube captions"})
    res = app_client.post("/api/jobs", json={"collections": [{"title": "Talk", "url": "u", "source": "youtube",
                                                              "items": [job_item()]}]})
    batch = db.get_batch(res.json()["batches"][0])
    jid = batch["jobs"][0]["id"]
    wait(lambda: app_client.get(f"/api/jobs/{jid}").json()["status"] == "done")
    wait(lambda: any(s["status"] == "done" for s in app_client.get(f"/api/jobs/{jid}").json()["summaries"]))
    detail = app_client.get(f"/api/jobs/{jid}").json()
    assert detail["paragraphs"][0]["text"].startswith("Hello there.")
    assert "test summary" in detail["summaries"][0]["content"]
    assert "Hello there." in received["chat"][-1]["messages"][1]["content"]
    assert app_client.get(f"/api/jobs/{jid}/export/srt").text.startswith("1\n00:00:01,000")
    assert "attachment" in app_client.get(f"/api/jobs/{jid}/export/md").headers["content-disposition"]
    assert app_client.post(f"/api/jobs/{jid}/ask", json={"question": "what?"}).json()["answer"]
    assert app_client.get("/api/search?q=hello").json()["results"][0]["id"] == jid
    assert app_client.delete(f"/api/jobs/{jid}").json()["ok"]
    assert not db.get_batch(batch["id"])


def test_failure_does_not_stop_queue_and_playlist_rollup(app_client, monkeypatch):
    def fake(url, settings):
        if url.endswith("bad000000000"[:11]):
            raise audio.Unavailable("Nope")
        return {"segments": [{"start": 0, "end": 2, "text": f"Words of {url[-3:]}."}], "language": "en",
                "method": "YouTube captions"}

    monkeypatch.setattr(worker, "transcript_for", lambda job, f, s, t: fake(job["url"], s))
    urls = ["https://youtu.be/one00000000", "https://youtu.be/bad00000000", "https://youtu.be/two00000000"]
    bid = app_client.post("/api/jobs", json={"collections": [{
        "title": "List", "url": "u", "source": "youtube", "is_collection": True,
        "items": [job_item(u) for u in urls]}]}).json()["batches"][0]
    wait(lambda: not any(j["status"] in ("queued", "working") for j in app_client.get(f"/api/batches/{bid}").json()["jobs"]))
    states = [j["status"] for j in app_client.get(f"/api/batches/{bid}").json()["jobs"]]
    assert states == ["done", "failed", "done"]
    wait(lambda: any(s["status"] == "done" and s["style"] == "collection"
                     for s in app_client.get(f"/api/batches/{bid}").json()["summaries"]))
    zipped = app_client.get(f"/api/batches/{bid}/export")
    import io
    import zipfile
    names = zipfile.ZipFile(io.BytesIO(zipped.content)).namelist()
    assert len({n.split("/")[0] for n in names}) == 1 and len(names) == 3


def test_cancel_stops_running_job(app_client, monkeypatch):
    def slow(job, folder, settings, tick):
        for i in range(200):
            time.sleep(0.05)
            tick("Transcribing", i / 200)
        raise AssertionError("not cancelled")

    monkeypatch.setattr(worker, "transcript_for", slow)
    bid = app_client.post("/api/jobs", json={"collections": [{"title": "S", "items": [job_item()]}]}).json()["batches"][0]
    jid = db.get_batch(bid)["jobs"][0]["id"]
    wait(lambda: db.job_status(jid) == "working")
    app_client.post(f"/api/jobs/{jid}/cancel")
    wait(lambda: db.get_job(jid)["status"] == "cancelled")
    time.sleep(1)
    assert db.get_job(jid)["error"] is None


def test_foreign_host_and_bad_links_are_refused(app_client):
    assert app_client.get("/api/status", headers={"Host": "evil.example"}).status_code == 400
    assert app_client.post("/api/resolve", json={"text": "nothing here"}).status_code == 400
    res = app_client.post("/api/resolve", json={"text": "https://example.com/video"})
    assert res.json()["errors"][0]["error"].startswith("Only YouTube") and not res.json()["collections"]
    bad = app_client.post("/api/jobs", json={"collections": [{"items": [{"url": "http://169.254.169.254/", "source": "youtube"}]}]})
    assert bad.status_code == 400


def test_settings_mask_keys(app_client):
    app_client.put("/api/settings", json={"groq_key": "gsk_secret1234"})
    shown = app_client.get("/api/settings").json()
    assert shown["groq_key"] == "••••1234"
    app_client.put("/api/settings", json=shown)
    assert config.load_settings()["groq_key"] == "gsk_secret1234"
    app_client.put("/api/settings", json={"groq_key": ""})


def test_ytdlp_download_and_cloud_transcription(upstream, monkeypatch):
    folder = Path(tempfile.mkdtemp())
    path = audio.download_with_ytdlp(upstream + "/tone.mp3", folder, {}, lambda *_: None)
    assert path.stat().st_size > 1000
    monkeypatch.setitem(audio.CLOUD, "groq", (upstream + "/audio/transcriptions", "m", "groq_key", "Groq"))
    out = audio.transcribe(path, folder, {"transcriber": "groq", "groq_key": "k"}, lambda *_: None)
    assert out["segments"][0]["text"].strip() == "Spoken words here." and received["uploads"]


def test_split_offsets(monkeypatch, upstream):
    monkeypatch.setattr(audio, "split_for_upload", lambda p, f: [Path(__file__), Path(__file__)])
    monkeypatch.setitem(audio.CLOUD, "groq", (upstream + "/audio/transcriptions", "m", "groq_key", "Groq"))
    out = audio.transcribe_cloud(Path(__file__), Path("."), "groq", {"groq_key": "k"}, lambda *_: None)
    assert [s["start"] for s in out["segments"]] == [1, 1201]


def test_claude_provider_shape(upstream, monkeypatch):
    monkeypatch.setitem(summarize.PROVIDERS["anthropic"], "base", upstream)
    out = summarize.chat([{"role": "user", "content": "hi"}], {"ai_provider": "anthropic", "anthropic_key": "k"})
    assert "From Claude" in out and received["chat"][-1]["system"]


def mock_client(handler):
    return lambda settings, timeout=30: httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_spotify_playlist_track_and_episode(monkeypatch):
    def handler(request):
        if "/embed/playlist/" in str(request.url):
            data = {"props": {"pageProps": {"state": {"data": {"entity": {"title": "Mix", "uri": "spotify:playlist:1",
                    "trackList": [{"uri": "spotify:track:aaaaaaaaaaaaaaaaaaaaaa", "title": "Song", "subtitle": "Band",
                                   "duration": 200000},
                                  {"uri": "spotify:episode:bbbbbbbbbbbbbbbbbbbbbb", "title": "Pod", "subtitle": "Show",
                                   "duration": 900000}]}}}}}}
            return httpx.Response(200, text=f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>')
        return httpx.Response(404)

    monkeypatch.setattr(sources, "client", mock_client(handler))
    res = sources.resolve("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=x", {})
    assert res["is_collection"] and [i["kind"] for i in res["items"]] == ["track", "podcast"]
    assert res["items"][0]["meta"]["artist"] == "Band"


def test_lyrics_lookup(monkeypatch):
    def handler(request):
        return httpx.Response(200, json=[
            {"duration": 300, "syncedLyrics": "[00:01.00]Wrong cut"},
            {"duration": 201, "syncedLyrics": "[00:01.00]Right line\n[00:03.00]Next line", "plainLyrics": "x"}])

    monkeypatch.setattr(captions, "client", mock_client(handler))
    got = captions.lyrics("Band, Guest", "Song - 2011 Remaster", 200, {})
    assert got["segments"][0]["text"] == "Right line"


def test_episode_found_through_feed(monkeypatch):
    def handler(request):
        url = str(request.url)
        if "podcastEpisode" in url:
            return httpx.Response(200, json={"results": []})
        if "itunes" in url:
            return httpx.Response(200, json={"results": [{"collectionName": "The Show", "feedUrl": "https://feed.test/rss"}]})
        return httpx.Response(200, content=b'<rss><channel><item><title>12: Big Idea</title>'
                                           b'<enclosure url="https://cdn.test/12.mp3"/></item></channel></rss>')

    monkeypatch.setattr(podcasts, "client", mock_client(handler))
    assert podcasts.find_episode_audio("Big Idea", "The Show", {}) == "https://cdn.test/12.mp3"


def test_exclusive_episode_explains_itself(monkeypatch, tmp_path):
    monkeypatch.setattr(podcasts, "find_episode_audio", lambda *a: None)
    job = {"source": "spotify", "kind": "podcast", "url": "u", "title": "T", "meta": {"show": "S"}}
    with pytest.raises(audio.Unavailable, match="Spotify exclusive"):
        worker.transcript_for(job, tmp_path, {}, lambda *_: None)
