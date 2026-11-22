"""Run with: python -m pytest tests  (network not required)."""
import os
import tempfile

os.environ["TRANSCRIPTRICK_DATA"] = tempfile.mkdtemp()

from app import captions, exports, podcasts, sources, summarize, timing  # noqa: E402


def test_find_links_mixed_text():
    text = """check https://youtu.be/dQw4w9WgXcQ?si=abc, and
    open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=1 then soundcloud.com/a/b."""
    assert sources.find_links(text) == ["https://youtu.be/dQw4w9WgXcQ?si=abc",
                                        "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=1",
                                        "https://soundcloud.com/a/b"]


def test_sources():
    assert sources.source_of("https://music.youtube.com/watch?v=x") == "youtube"
    assert sources.source_of("https://on.soundcloud.com/abc") == "soundcloud"
    assert sources.source_of("https://spotify.link/abc") == "spotify"
    assert sources.source_of("https://notyoutube.com/watch") is None
    assert sources.source_of("https://youtube.com.evil.net/watch") is None


def test_youtube_urls():
    c = sources.clean_youtube_url
    assert c("https://youtu.be/abc?t=4") == "https://www.youtube.com/watch?v=abc"
    assert c("https://www.youtube.com/watch?v=abc&list=PLx1") == "https://www.youtube.com/playlist?list=PLx1"
    assert c("https://www.youtube.com/watch?v=abc&list=RDabc&start_radio=1") == "https://www.youtube.com/watch?v=abc"
    assert c("https://www.youtube.com/@mkbhd") == "https://www.youtube.com/@mkbhd/videos"
    assert c("https://www.youtube.com/@mkbhd/shorts") == "https://www.youtube.com/@mkbhd/shorts"


def test_spotify_parse_and_entity():
    assert sources.parse_spotify("https://open.spotify.com/intl-de/track/4uLU6hMCjMI75M1A2tKUQC?si=1") == (
        "track", "4uLU6hMCjMI75M1A2tKUQC")
    page = ('<html><script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"state":{"data":{"entity":'
            '{"type":"playlist","title":"Mix","uri":"spotify:playlist:1","trackList":[{"uri":"spotify:track:'
            'aaaaaaaaaaaaaaaaaaaaaa","title":"Song","subtitle":"Band","duration":180000}]}}}}}}</script></html>')
    entity = sources.find_entity(sources.next_data(page))
    assert entity["title"] == "Mix" and entity["trackList"][0]["title"] == "Song"
    moved = {"a": [{"b": {"uri": "spotify:track:x", "name": "Moved"}}]}
    assert sources.find_entity(moved)["name"] == "Moved"
    item = sources.spotify_item("track", "abc", "Song", "Band", 180000, None)
    assert item["duration"] == 180 and item["meta"]["artist"] == "Band" and item["kind"] == "track"


def test_flatten_channel_tabs():
    info = {"entries": [{"_type": "playlist", "entries": [{"id": "a"}, None, {"id": "b"}]}, {"id": "c"}]}
    out = []
    sources._flatten(info, 2, out)
    assert [e["id"] for e in out] == ["a", "b"]


def test_transcript_choice_prefers_spoken_language():
    class T:
        def __init__(self, code, gen):
            self.language_code, self.is_generated = code, gen

    picked = captions.choose_transcript([T("en", False), T("de", False), T("de", True)], "")
    assert (picked.language_code, picked.is_generated) == ("de", False)
    assert captions.choose_transcript([T("en", False), T("de", True)], "en").language_code == "en"


def test_json3_and_track_pick():
    assert captions.parse_json3({"events": [{"tStartMs": 1000, "dDurationMs": 2000, "segs": [{"utf8": "hi "},
                                 {"utf8": "there"}]}, {"tStartMs": 3000}]}) == [
        {"start": 1.0, "end": 3.0, "text": "hi there"}]
    info = {"language": "fr", "automatic_captions": {
        "en": [{"ext": "json3", "url": "e"}], "fr": [{"ext": "vtt", "url": "v"}, {"ext": "json3", "url": "f"}]}}
    assert captions.pick_track(info, "")[:2] == ("fr", "f")


def test_timing():
    assert timing.clock(65) == "1:05" and timing.clock(3725) == "1:02:05"
    raw = [{"start": 0, "end": 1, "text": "[Music]"}, {"start": 1, "end": 2, "text": "Hello &amp; welcome."},
           {"start": 2, "end": 3, "text": "Hello &amp; welcome."}]
    assert timing.clean_segments(raw) == [{"start": 1.0, "end": 3.0, "text": "Hello & welcome."}]
    segs = [{"start": i * 10, "end": i * 10 + 9, "text": f"Line {i}."} for i in range(10)]
    groups = timing.paragraphs(segs)
    assert 1 < len(groups) < 10 and groups[0]["start"] == 0
    lrc = timing.parse_lrc("[ar:Band]\n[00:10.50]First\n[00:12.00][00:20.00]Again\n")
    assert [s["text"] for s in lrc] == ["First", "Again", "Again"] and lrc[0]["end"] == 12.0


def test_rss_match():
    feed = b"""<rss><channel><item><title>Ep 12: The Big Idea</title><enclosure url="http://x/12.mp3"/></item>
    <item><title>Other</title><enclosure url="http://x/o.mp3"/></item></channel></rss>"""
    assert podcasts.best_rss_match(feed, "The Big Idea") == "http://x/12.mp3"
    assert podcasts.best_rss_match(feed, "Totally unrelated title") is None


def test_exports():
    segs = [{"start": 1.5, "end": 3.25, "text": "Hi"}]
    assert exports.to_srt(segs) == "1\n00:00:01,500 --> 00:00:03,250\nHi\n"
    assert exports.to_vtt(segs).startswith("WEBVTT\n\n00:00:01.500 --> 00:00:03.250\nHi")
    assert exports.safe_name('a/b:c?') == "abc"


def test_chunks_and_relevance():
    lines = [f"[0:{i:02d}] word{i} " + "x" * 50 for i in range(20)]
    parts = summarize.chunks(lines, 200)
    assert "\n".join(parts).split("\n") == lines and all(len(p) <= 260 for p in parts)
    assert any("word7" in ln for ln in summarize.relevant(lines, "what about word7?", 100))


def test_friendly_errors():
    f = sources.friendly_ydl_error
    assert "reached" in f(Exception("ERROR: [youtube] x: Unable to download API page: Tunnel connection failed: 403"))
    assert "age restricted" in f(Exception("Sign in to confirm your age. This video may be inappropriate for some users."))
    assert "signed-in" in f(Exception("Sign in to confirm you're not a bot"))
    assert "private" in f(Exception("Private video. Sign in if you've been granted access"))
    assert "age restricted" not in f(Exception("Some message about a page"))


def test_repeats_far_apart_are_kept():
    raw = [{"start": 1, "end": 2, "text": "Same line."}, {"start": 90, "end": 92, "text": "Same line."}]
    assert len(timing.clean_segments(raw)) == 2
