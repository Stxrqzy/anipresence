"""Extension-bridge flow through the controller: an HTTP report from a
"normal browser" tab becomes the current detection session (no CDP involved)."""
import http.client
import json
import os
import sys
import time

import pytest
from PySide6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.browser.detectors import VideoState
from src.browser.extension import ExtensionBridge
from src.config import ConfigManager
from src.core.controller import Controller
from src.core.recent import RecentHistory
from src.ui.bridge import UiBridge


@pytest.fixture(scope="module")
def app():
    existing = QApplication.instance()
    if existing is not None:
        return existing
    return QApplication(sys.argv)


def make_controller(tmp_path):
    cfg = ConfigManager(str(tmp_path / "config.json"))
    cfg.set(rpc_enabled=False, detection_enabled=True, extension_port=0)
    ctrl = Controller(cfg, UiBridge())
    ctrl.recent = RecentHistory(path=str(tmp_path / "recent.json"))
    return ctrl


def _payload(video=None):
    return {
        "url": "https://gogoanime.example/watch/demon-slayer-episode-5",
        "title": "Demon Slayer - Watch Episode 5",
        "visibility": "visible",
        "focused": True,
        "video": video
        or {
            "hasVideo": True,
            "currentTime": 45.0,
            "duration": 1200.0,
            "paused": False,
            "ended": False,
        },
    }


def test_controller_tick_extension_direct(tmp_path, app, monkeypatch):
    ctrl = make_controller(tmp_path)
    monkeypatch.setattr(ctrl, "_resolve_anime", lambda detection: (None, 0.0))

    ctrl._tick_extension([("tab-1", _payload())])

    assert ctrl._current is not None
    detection = ctrl._current.detection
    assert detection.website == "GoGoAnime"
    assert detection.episode == 5
    assert detection.video.has_video


def test_controller_skips_navigation_page(tmp_path, app, monkeypatch):
    """A site home page autoplays a trailer <video>, so it looks like a real
    tab - but it must not produce a session (no anime is being identified)."""
    ctrl = make_controller(tmp_path)
    calls = {"n": 0}

    def fragile(detection):
        calls["n"] += 1
        raise AssertionError("must not resolve on a home page")

    monkeypatch.setattr(ctrl, "_resolve_anime", fragile)

    home_payload = {
        "url": "https://miruro.example/",
        "title": "Miruro · Watch Free Anime Online · Stream Subbed & Dubbed Anime in HD",
        "visibility": "visible",
        "focused": True,
        "video": {"hasVideo": True, "paused": False},
    }
    ctrl._tick_extension([("tab-home", home_payload)])

    assert ctrl._current is None
    assert calls["n"] == 0


def test_negative_anilist_cache_skips_repeat_search(tmp_path, app, monkeypatch):
    from src.anime.anilist import Media

    ctrl = make_controller(tmp_path)
    searches = {"n": 0}

    class FakeAniListSearch:
        def search_media(self, term, per_page=5):
            searches["n"] += 1
            return []

    fake = FakeAniListSearch()
    ctrl.anilist = fake

    class FakeDetection:
        anilist_id = None
        cleaned_title = "Totally Imaginary Anime Series XYZ"
        raw_title = ""

    first = ctrl._resolve_anime(FakeDetection())
    assert first == (None, 0.0)
    n_after_first = searches["n"]
    assert n_after_first >= 1

    second = ctrl._resolve_anime(FakeDetection())
    assert second == (None, 0.0)
    assert searches["n"] == n_after_first  # cached miss - no repeat API call


def test_negative_anilist_cache_expires(tmp_path, app, monkeypatch):
    ctrl = make_controller(tmp_path)

    class FakeAniListSearch:
        def search_media(self, term, per_page=5):
            searches["n"] += 1
            return []

    searches = {"n": 0}
    ctrl.anilist = FakeAniListSearch()

    class FakeDetection:
        anilist_id = None
        cleaned_title = "Another Unmatchable Title Here"
        raw_title = ""

    ctrl._resolve_anime(FakeDetection())
    n_after_first = searches["n"]
    assert n_after_first >= 1
    key = "another unmatchable title here"
    assert key in ctrl._anilist_miss

    # Force the miss to expire, then the next query hits the API again.
    ctrl._anilist_miss[key] = time.time() - 1000.0
    ctrl._resolve_anime(FakeDetection())
    assert searches["n"] > n_after_first


def test_sync_presence_timestamps_anchor_to_position(tmp_path, app, monkeypatch):
    """Seeking must not shrink the episode: end - start always equals the real
    duration because both timestamps are anchored to the video position, not to
    the wall-clock moment the session started."""
    from src.browser.detectors import Detection
    from src.core.controller import CurrentSession

    ctrl = make_controller(tmp_path)
    cfg = ctrl.cfg.data
    cfg.rpc_enabled = True
    cfg.rpc_broadcast = "discord"
    cfg.discord_application_id = "1111111111"

    sent = {}

    def fake_send(details, state, cover, title, start, end, buttons):
        sent.update(details=details, state=state, start=start, end=end)

    monkeypatch.setattr(ctrl, "_send_rpc", fake_send)
    monkeypatch.setattr(ctrl, "_set_discord_status", lambda *a, **k: None)

    def make_session(position):
        detection = Detection(
            website="GoGoAnime",
            url="https://gogoanime.example/watch/demon-slayer-episode-5",
            raw_title="Demon Slayer - Watch Episode 5",
            cleaned_title="Demon Slayer",
            episode=5,
            video=VideoState(has_video=True, current_time=position, duration=1440.0, paused=False),
        )
        return CurrentSession(detection, None, 100.0, time.time() - 120.0, "key")

    ctrl._current = make_session(0.0)
    ctrl._last_sent = None
    ctrl._sync_presence()
    start0, end0 = sent["start"], sent["end"]
    assert end0 - start0 == 1440  # full 24-minute episode

    # Jump to the halfway mark (12:00) - the reported glitch.
    ctrl._current = make_session(720.0)
    sent.clear()
    ctrl._sync_presence()
    start1, end1 = sent["start"], sent["end"]
    assert end1 - start1 == 1440  # still the full 24 minutes
    assert abs((int(time.time()) - start1) - 720) < 3  # elapsed tracks the position


def test_library_sections_group_by_status(tmp_path, app):
    from src.anime.anilist import Media

    ctrl = make_controller(tmp_path)
    media = Media(id=1, title_romaji="Show A", episodes=12, cover_large="https://c/a.jpg")
    entries = [
        {"media": media, "status": "CURRENT", "progress": 5},
        {"media": media, "status": "PLANNING", "progress": 0},
        {"media": media, "status": "COMPLETED", "progress": 12},
        {"media": media, "status": "PAUSED", "progress": 3},
        {"media": media, "status": "DROPPED", "progress": 1},
    ]
    sections = ctrl._build_library_sections(entries)
    assert sections["Watching"][0]["progress"] == 5
    assert sections["Planning"][0]["progress"] == 0
    assert sections["Completed"][0]["progress"] == 12
    assert sections["Paused"][0]["progress"] == 3
    assert sections["Dropped"][0]["progress"] == 1
    assert sections["Watching"][0]["anilist_id"] == 1
    assert sections["Watching"][0]["title"] == "Show A"


def test_refresh_library_emits_sections(tmp_path, app, monkeypatch):
    from src.anime.anilist import Media

    ctrl = make_controller(tmp_path)
    ctrl._active_account = "someone"

    class FakeAniList:
        def fetch_user_list(self, username):
            media = Media(id=7, title_romaji="Show B", episodes=24)
            return [{"media": media, "status": "CURRENT", "progress": 9}]

    ctrl.anilist = FakeAniList()

    class Capture:
        def __init__(self):
            self.calls = []

        def emit(self, *args):
            self.calls.append(args)

    cap = Capture()
    monkeypatch.setattr(ctrl.bridge, "library_loaded", cap)
    ctrl.refresh_library()

    deadline = time.time() + 5
    while not cap.calls and time.time() < deadline:
        time.sleep(0.05)
    assert cap.calls, "library_loaded never emitted"
    payload = cap.calls[0][0]
    assert payload["username"] == "someone"
    watching = payload["sections"]["Watching"]
    assert watching and watching[0]["title"] == "Show B"
    assert watching[0]["progress"] == 9


def test_current_season_mapping():
    from datetime import date

    from src.core.controller import Controller

    assert Controller._current_season(date(2026, 1, 15)) == ("WINTER", 2026)
    assert Controller._current_season(date(2026, 5, 1)) == ("SPRING", 2026)
    assert Controller._current_season(date(2026, 8, 16)) == ("SUMMER", 2026)
    assert Controller._current_season(date(2026, 12, 1)) == ("FALL", 2026)


def test_build_calendar_entries_marks_library(tmp_path, app):
    from src.anime.anilist import Media

    ctrl = make_controller(tmp_path)
    seasonal = [
        Media(id=1, title_romaji="Airs Soon", next_airing_at=100, next_airing_episode=2),
        Media(id=2, title_romaji="No Date", next_airing_at=None),
    ]
    library = [{"media": Media(id=1, title_romaji="Airs Soon"), "status": "CURRENT", "progress": 1}]
    entries = ctrl._build_calendar_entries(seasonal, library)
    assert entries[0]["anilist_id"] == 1  # soonest-to-air first
    assert entries[0]["in_library"] is True
    assert entries[0]["library_status"] == "CURRENT"
    assert entries[1]["anilist_id"] == 2
    assert entries[1]["in_library"] is False


def test_refresh_calendar_emits_entries(tmp_path, app, monkeypatch):
    from src.anime.anilist import Media

    ctrl = make_controller(tmp_path)
    ctrl._active_account = "someone"

    class FakeAniList:
        def fetch_seasonal(self, season, year):
            return [Media(id=3, title_romaji="Seasonal Hit", next_airing_at=500, next_airing_episode=1)]

        def fetch_user_list(self, username):
            return []

    ctrl.anilist = FakeAniList()

    class Capture:
        def __init__(self):
            self.calls = []

        def emit(self, *args):
            self.calls.append(args)

    cap = Capture()
    monkeypatch.setattr(ctrl.bridge, "calendar_loaded", cap)
    ctrl.refresh_calendar()

    deadline = time.time() + 5
    while not cap.calls and time.time() < deadline:
        time.sleep(0.05)
    assert cap.calls, "calendar_loaded never emitted"
    payload = cap.calls[0][0]
    assert "SUMMER" in payload["season"]
    assert payload["entries"][0]["title"] == "Seasonal Hit"


def test_now_playing_snapshot_flow(tmp_path, app, monkeypatch):
    """A live session yields an active snapshot; privacy/adult rules apply."""
    from src.anime.anilist import Media
    from src.browser.detectors import Detection

    ctrl = make_controller(tmp_path)
    detection = Detection(
        website="gogoanime",
        url="https://gogoanime.example/watch/demon-slayer-episode-5",
        raw_title="Demon Slayer - Watch Episode 5",
        cleaned_title="Demon Slayer",
        episode=5,
        video=VideoState(has_video=True, current_time=45.0, duration=1200.0, paused=False),
    )
    media = Media(id=9, title_romaji="Demon Slayer", episodes=26,
                  cover_extra_large="https://cdn.example/c.jpg", site_url="https://anilist.co/anime/9")
    ctrl._current = type("S", (), {"detection": detection, "media": media})()

    snap = ctrl.now_playing()
    assert snap["active"] is True
    assert snap["title"] == "Demon Slayer"
    assert snap["status"] == "playing"
    assert snap["position"] == 45.0
    assert snap["duration"] == 1200.0
    assert snap["cover"] == "https://cdn.example/c.jpg"
    assert snap["episode"] == 5
    assert snap["version"] == "2.0.5"

    # Privacy mode -> fully idle.
    ctrl.cfg.set(privacy_mode=True)
    assert ctrl.now_playing()["active"] is False
    ctrl.cfg.set(privacy_mode=False)

    # hide_adult_content + adult media -> playback visible, identity withheld.
    adult = Media(id=10, title_romaji="Ecchi Show", is_adult=True,
                  cover_extra_large="https://cdn.example/a.jpg")
    ctrl._current = type("S", (), {"detection": detection, "media": adult})()
    ctrl.cfg.set(hide_adult_content=True)
    snap = ctrl.now_playing()
    assert snap["active"] is True
    assert snap["title"] == ""
    assert snap["cover"] == ""
    assert snap["hidden"] is True

    # No session -> idle.
    ctrl._current = None
    assert ctrl.now_playing()["active"] is False


def test_now_http_endpoints(tmp_path, app, monkeypatch):
    """GET /now.json and /now work over the real bridge server (no token)."""
    ctrl = make_controller(tmp_path)
    ctrl.extension.start()
    assert ctrl.extension.running

    def fake_snapshot():
        return {"active": True, "title": "Demon Slayer", "status": "playing"}

    ctrl.extension.now_provider = fake_snapshot

    try:
        port = ctrl.extension.bound_port
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            conn.request("GET", "/now.json")
            resp = conn.getresponse()
            assert resp.status == 200
            body = json.loads(resp.read().decode("utf-8"))
            assert body["title"] == "Demon Slayer"

            conn.request("GET", "/now")
            resp = conn.getresponse()
            assert resp.status == 200
            page = resp.read().decode("utf-8")
            assert "Now Playing" in page
            assert "/now.json" in page

            conn.request("GET", "/nope")
            assert conn.getresponse().status == 404
        finally:
            conn.close()
    finally:
        ctrl.extension.stop()


def test_vencord_presence_name_is_anime(tmp_path, app, monkeypatch):
    """The Discord activity must read "Watching Anime", not "Watching AniPresence"."""
    from src.browser.detectors import Detection
    from src.core.controller import CurrentSession

    ctrl = make_controller(tmp_path)
    cfg = ctrl.cfg.data
    cfg.rpc_enabled = True
    cfg.rpc_broadcast = "vencord"

    sent = {}

    def fake_send(activity, sig):
        sent.update(activity)
        return True

    monkeypatch.setattr(ctrl.vencord, "send", fake_send)
    monkeypatch.setattr(ctrl, "_set_discord_status", lambda *a, **k: None)

    detection = Detection(
        website="GoGoAnime",
        url="https://gogoanime.example/watch/demon-slayer-episode-5",
        raw_title="Demon Slayer - Watch Episode 5",
        cleaned_title="Demon Slayer",
        episode=5,
        video=VideoState(has_video=True, current_time=100.0, duration=1440.0, paused=False),
    )
    ctrl._current = CurrentSession(detection, None, 100.0, time.time() - 100.0, "key")
    ctrl._last_sent = None
    ctrl._sync_presence()
    assert sent.get("name") == "Anime"
    assert sent.get("details") == "Demon Slayer"  # no "Watching: " prefix


def test_prompt_match_emits_and_throttles(tmp_path, app, monkeypatch):
    from src.anime.anilist import Media
    from src.browser.detectors import Detection

    ctrl = make_controller(tmp_path)

    class Capture:
        def __init__(self):
            self.calls = []

        def emit(self, *args):
            self.calls.append(args)

    cap = Capture()
    monkeypatch.setattr(ctrl.bridge, "match_pending", cap)

    detection = Detection(
        website="gogoanime",
        url="https://gogoanime.example/watch/x",
        raw_title="X",
        cleaned_title="X",
        video=VideoState(),
    )
    results = [
        Media(id=1, title_romaji="One Piece", cover_large="https://c/op.jpg",
              site_url="https://anilist.co/anime/1"),
    ]
    ctrl._prompt_match(detection, results, "X", results[0], 55.0)
    assert len(cap.calls) == 1
    payload = cap.calls[0][0]
    assert payload["url"] == detection.url
    assert payload["confidence"] == 55
    assert payload["candidates"][0]["title"] == "One Piece"
    assert payload["candidates"][0]["is_current"] is True
    assert payload["candidates"][0]["cover"] == "https://c/op.jpg"

    ctrl._prompt_match(detection, results, "X", results[0], 55.0)
    assert len(cap.calls) == 1  # throttled to once per minute


def test_build_library_sections_includes_keywords(tmp_path, app):
    from src.anime.anilist import Media

    ctrl = make_controller(tmp_path)
    media = Media(id=1, title_romaji="Show A", episodes=12,
                  genres=["Action", "Drama"], mean_score=80)
    sections = ctrl._build_library_sections(
        [{"media": media, "status": "CURRENT", "progress": 5}]
    )
    entry = sections["Watching"][0]
    assert "show a" in entry["keywords"]
    assert "action" in entry["keywords"]
    assert "80" in entry["keywords"]


def test_full_http_roundtrip(tmp_path, app, monkeypatch):
    ctrl = make_controller(tmp_path)
    monkeypatch.setattr(ctrl, "_resolve_anime", lambda detection: (None, 0.0))
    ctrl.extension.start()
    assert ctrl.extension.running
    port = ctrl.extension.bound_port
    token = ctrl.extension.token
    assert token  # auto-generated and persisted

    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST",
            "/report",
            body=json.dumps({"tabs": [{"tab": "tab-9", **dict(_payload())}]}),
            headers={"Content-Type": "application/json", "X-AniPresence-Token": token},
        )
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 200

        tabs = ctrl.extension.live_tabs()
        assert len(tabs) == 1
        ctrl._tick_extension(tabs)
        assert ctrl._current is not None
        assert ctrl._current.detection.episode == 5
    finally:
        ctrl.extension.stop()