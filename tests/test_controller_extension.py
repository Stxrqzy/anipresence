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