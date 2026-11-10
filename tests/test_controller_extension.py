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

from src import __version__
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


@pytest.fixture(autouse=True)
def isolate_app_data(tmp_path, monkeypatch):
    monkeypatch.setattr('src.core.controller.APP_DIR', tmp_path)


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


def test_episode_completion_requires_ninety_percent():
    from src.browser.detectors import VideoState

    assert not Controller._episode_complete(
        VideoState(has_video=True, current_time=899, duration=1000)
    )
    assert Controller._episode_complete(
        VideoState(has_video=True, current_time=900, duration=1000)
    )
    assert not Controller._episode_complete(
        VideoState(has_video=True, current_time=10, duration=1000, ended=True)
    )


def test_anilist_sync_threshold_deduplication_and_token_change(tmp_path, app, monkeypatch):
    from types import SimpleNamespace
    from src.anime.anilist import Media
    from src.browser.detectors import Detection
    from src.core.controller import CurrentSession

    ctrl = make_controller(tmp_path)
    ctrl.cfg.set(anilist_sync_enabled=True, anilist_access_token="first")
    calls = []

    class ImmediateThread:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    def client(access_token):
        return SimpleNamespace(
            sync_progress=lambda *args: calls.append((access_token, args)),
            _session=SimpleNamespace(close=lambda: None),
        )

    monkeypatch.setattr("src.core.controller.threading.Thread", ImmediateThread)
    monkeypatch.setattr("src.core.controller.AniListClient", client)
    video = VideoState(has_video=True, current_time=899, duration=1000)
    session = CurrentSession(Detection("site", "url", episode=1, video=video),
                             Media(id=42, episodes=12), 100, 0, "episode")
    ctrl._maybe_sync_anilist(session, video)
    assert not calls
    video.current_time = 900
    ctrl._maybe_sync_anilist(session, video)
    ctrl._maybe_sync_anilist(session, video)
    assert calls == [("first", (42, 1, 12))]
    ctrl.cfg.set(anilist_access_token="second", anilist_sync_enabled=False)
    ctrl._maybe_sync_anilist(session, video)
    assert len(calls) == 1
    ctrl.cfg.set(anilist_sync_enabled=True)
    ctrl._maybe_sync_anilist(session, video)
    assert calls[-1] == ("second", (42, 1, 12))


def test_failed_episode_can_retry_after_leaving_tab(tmp_path, app, monkeypatch):
    from types import SimpleNamespace
    from src.anime.anilist import Media
    from src.browser.detectors import Detection
    from src.core.controller import CurrentSession

    ctrl = make_controller(tmp_path)
    ctrl.cfg.set(anilist_access_token='token', anilist_sync_enabled=True, anilist_sync_account='Alice')
    calls = []
    class ImmediateThread:
        def __init__(self, target, **kw): self.target = target
        def start(self): self.target()
    def update(*args):
        calls.append(args)
        if len(calls) == 1:
            raise RuntimeError('offline')
        return True
    monkeypatch.setattr('src.core.controller.threading.Thread', ImmediateThread)
    monkeypatch.setattr('src.core.controller.AniListClient', lambda **kw: SimpleNamespace(
        sync_progress=update, _session=SimpleNamespace(close=lambda: None)))
    video = VideoState(has_video=True, duration=1000, current_time=950)
    session = CurrentSession(Detection('site', 'url', episode=5, video=video),
                             Media(id=20, title_romaji='Naruto', episodes=220), 100, 0, 'key')
    ctrl._maybe_sync_anilist(session, video)
    assert ctrl.sync_status()['pending'] == ['Naruto · Episode 5']
    assert ctrl.sync_status()['error'] == 'offline'
    ctrl._current = None
    ctrl._retry_pending_episodes()  # cooldown still applies
    assert len(calls) == 1
    ctrl.cfg.set(anilist_access_token='other')
    ctrl._retry_pending_episodes(force=True)
    assert len(calls) == 1  # never send another account's pending episode
    ctrl.cfg.set(anilist_access_token='token')
    ctrl._retry_pending_episodes(force=True)
    assert len(calls) == 2
    assert not ctrl.sync_status()['pending']
    assert not ctrl.sync_status()['error']
    assert ctrl.sync_status()['last_success'] > 0


def test_presence_options_and_pause_with_label_hidden(tmp_path, app, monkeypatch):
    from src.anime.anilist import Media
    from src.browser.detectors import Detection
    from src.core.controller import CurrentSession
    ctrl = make_controller(tmp_path)
    ctrl.cfg.set(rpc_enabled=True, discord_application_id='123', presence_episode_names=False,
                 presence_cover_art=False, presence_airing_dates=False, presence_paused_label=False)
    video = VideoState(has_video=True, current_time=60, duration=0, paused=False)
    detection = Detection('ani.pm', 'url', episode=3, video=video, episode_title='Episode name')
    media = Media(id=1, title_romaji='Anime', episodes=12, cover_large='https://example.com/cover.jpg',
                  next_airing_at=int(time.time())+3600, next_airing_episode=4)
    ctrl._current = CurrentSession(detection, media, 100, time.time(), 'key')
    sent = []
    monkeypatch.setattr(ctrl, '_send_rpc', lambda *args: sent.append(args))
    ctrl._sync_presence()
    assert sent[-1][1] == 'Episode 3 of 12'
    assert sent[-1][2] is None
    video.paused = True
    ctrl._sync_presence()
    assert len(sent) == 2  # identical text still clears the timer immediately
    assert sent[-1][4] is None and sent[-1][5] is None
    ctrl.cfg.set(presence_episode_names=True, presence_cover_art=True)
    ctrl._sync_presence()
    assert 'Episode name' in sent[-1][1]
    assert sent[-1][2] == media.cover_large


@pytest.mark.parametrize("paused", [True, False])
def test_anipm_episode_reaches_discord_and_updates_on_navigation(tmp_path, app, monkeypatch, paused):
    from src.anime.anilist import Media
    import src.core.controller as controller_module

    monkeypatch.setattr(controller_module, "APP_DIR", tmp_path)
    ctrl = make_controller(tmp_path)
    ctrl.cfg.set(rpc_enabled=True, rpc_broadcast="discord", discord_application_id="123")
    monkeypatch.setattr(ctrl, "_resolve_anime", lambda detection: (
        Media(id=20, title_romaji="NARUTO", episodes=220), 100,
    ))
    sent = []
    monkeypatch.setattr(ctrl, "_send_rpc", lambda *args: sent.append(args))
    payload = {
        "url": "https://ani.pm/anime/naruto-123?ep=e1.opaque",
        "title": "Watch NARUTO · ani.pm", "episode": 5,
        "episodeTitle": "A named episode",
        "video": {"hasVideo": True, "currentTime": 557, "duration": 1361, "paused": paused},
    }
    ctrl._tick_extension([("ani-tab", payload)])
    assert ctrl._current.detection.episode == 5
    assert "Episode 5" in sent[-1][1]
    assert "Episode 5 of 220 · A named episode" in sent[-1][1]
    if paused:
        assert "Paused" in sent[-1][1]
    payload.update(episode=6, episodeTitle="Next episode name", url="https://ani.pm/anime/naruto-123?ep=e1.next")
    ctrl._tick_extension([("ani-tab", payload)])
    assert ctrl._current.detection.episode == 6
    assert "Episode 6" in sent[-1][1]
    assert "Next episode name" in sent[-1][1]
    assert "A named episode" not in sent[-1][1]
    payload.update(episodeTitle="")
    ctrl._tick_extension([("ani-tab", payload)])
    assert "Next episode name" not in sent[-1][1]


def test_anipm_browser_metadata_readers():
    import shutil
    import subprocess
    from pathlib import Path
    from src.core.controller import PAGE_INFO_JS

    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required for extension JavaScript regression checks")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [node, str(root / "tests" / "extension_metadata.cjs")],
        input=PAGE_INFO_JS, text=True, capture_output=True, cwd=root,
    )
    assert result.returncode == 0, result.stdout + result.stderr


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


@pytest.mark.parametrize("duration", [1200, 0])
def test_pause_clears_timestamps_and_resume_restores_playhead(tmp_path, app, monkeypatch, duration):
    from types import SimpleNamespace
    from src.browser.detectors import Detection
    from src.core.controller import CurrentSession

    ctrl = make_controller(tmp_path)
    ctrl.cfg.set(rpc_enabled=True, rpc_broadcast="both", discord_application_id="123")
    rpc, vencord = [], []
    ctrl.discord = SimpleNamespace(is_connected=True, update=lambda **kw: rpc.append(kw))
    ctrl.vencord = SimpleNamespace(send=lambda activity, signature: vencord.append(activity) or True)
    clock = [2000]
    monkeypatch.setattr("src.core.controller.time.time", lambda: clock[0])
    video = VideoState(has_video=True, current_time=300, duration=duration, paused=False)
    detection = Detection("ani.pm", "https://ani.pm/anime/naruto?ep=opaque",
                          cleaned_title="Naruto", episode=5, video=video)
    ctrl._current = CurrentSession(detection, None, 100, 1500, "naruto-5")

    ctrl._sync_presence()
    assert "start" in rpc[-1]
    assert "start" in vencord[-1]["timestamps"]

    video.paused = True
    clock[0] += 1
    ctrl._sync_presence()
    assert len(rpc) == len(vencord) == 2  # pause bypasses the normal throttle
    assert "start" not in rpc[-1] and "end" not in rpc[-1]
    assert "timestamps" not in vencord[-1]
    assert rpc[-1]["state"] == "Paused · Episode 5"

    clock[0] += 60
    ctrl._sync_presence()
    assert "start" not in rpc[-1] and "timestamps" not in vencord[-1]

    video.paused = False
    clock[0] += 1
    ctrl._sync_presence()
    assert rpc[-1]["state"] == "Episode 5"
    if duration:
        assert clock[0] - rpc[-1]["start"] == 300
        assert rpc[-1]["end"] - rpc[-1]["start"] == duration
        assert vencord[-1]["timestamps"] == {"start": rpc[-1]["start"], "end": rpc[-1]["end"]}
    else:
        assert rpc[-1]["start"] == 1500
        assert "end" not in rpc[-1]


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


def test_sync_anilist_prefers_token_and_emits(tmp_path, app, monkeypatch):
    from src.anime.anilist import Media

    ctrl = make_controller(tmp_path)
    ctrl.cfg.set(anilist_access_token="tok", anilist_username="someone")
    ctrl._active_account = "someone"

    seen = {}

    class FakeAniList:
        def __init__(self, access_token=""):
            seen["token"] = access_token
            self._session = type("S", (), {"close": lambda self: None})()

        def fetch_viewer_list(self):
            seen["method"] = "viewer"
            media = Media(id=5, title_romaji="Pull Show", episodes=12)
            return ([{"media": media, "status": "CURRENT", "progress": 3}], "Alice")

        def fetch_user_list(self, username):
            seen["method"] = "public"
            return []

    monkeypatch.setattr("src.core.controller.AniListClient", FakeAniList)

    class Capture:
        def __init__(self):
            self.calls = []

        def emit(self, *args):
            self.calls.append(args)

    synced, library = Capture(), Capture()
    monkeypatch.setattr(ctrl.bridge, "anilist_synced", synced)
    monkeypatch.setattr(ctrl.bridge, "library_loaded", library)

    ctrl.sync_anilist()

    deadline = time.time() + 5
    while not synced.calls and time.time() < deadline:
        time.sleep(0.05)
    assert synced.calls, "anilist_synced never emitted"
    assert seen["token"] == "tok"
    assert seen["method"] == "viewer"  # token path, not the public username
    assert synced.calls[0][0]["account"] == "Alice"
    assert synced.calls[0][0]["titles"] == 1
    # The pull also refreshes the Library tab from the same fetch.
    assert library.calls, "library_loaded never emitted"
    assert library.calls[0][0]["sections"]["Watching"][0]["progress"] == 3


def test_sync_anilist_without_account_reports(tmp_path, app, monkeypatch):
    ctrl = make_controller(tmp_path)
    ctrl.cfg.set(anilist_access_token="", anilist_username="")
    ctrl._active_account = ""

    class Capture:
        def __init__(self):
            self.calls = []

        def emit(self, *args):
            self.calls.append(args)

    synced = Capture()
    monkeypatch.setattr(ctrl.bridge, "anilist_synced", synced)
    ctrl.sync_anilist()
    assert synced.calls
    assert "No AniList account" in synced.calls[0][0]["message"]


def test_activity_refresh_preserves_cache_on_failure_and_throttles(tmp_path, app, monkeypatch):
    ctrl = make_controller(tmp_path)
    ctrl.switch_anilist_account("Alice")
    ctrl.watchlog.set_activity({"items": [], "fetched_at": 1})
    attempts = []
    class Client:
        def __init__(self, **kwargs):
            self._session = type("Session", (), {"close": lambda self: None})()
        def fetch_user_activity(self, account):
            attempts.append(account)
            raise RuntimeError("offline")
    class Thread:
        def __init__(self, target, **kwargs): self.target = target
        def start(self): self.target()
    monkeypatch.setattr("src.core.controller.AniListClient", Client)
    monkeypatch.setattr("src.core.controller.threading.Thread", Thread)
    ctrl.refresh_activity_stats()
    ctrl.refresh_activity_stats()
    assert attempts == ["Alice"]
    assert ctrl.watchlog.activity() == {"items": [], "fetched_at": 1}
    ctrl.refresh_activity_stats(force=True)
    assert attempts == ["Alice", "Alice"]


def test_activity_refresh_ignores_result_after_profile_switch(tmp_path, app, monkeypatch):
    ctrl = make_controller(tmp_path)
    ctrl.switch_anilist_account("Alice")
    old_log = ctrl.watchlog
    pending = []
    class Client:
        def __init__(self, **kwargs):
            self._session = type("Session", (), {"close": lambda self: None})()
        def fetch_user_activity(self, account):
            return {"items": [], "account": account, "fetched_at": time.time()}
    class Thread:
        def __init__(self, target, **kwargs): pending.append(target)
        def start(self): pass
    monkeypatch.setattr("src.core.controller.AniListClient", Client)
    monkeypatch.setattr("src.core.controller.threading.Thread", Thread)
    ctrl.refresh_activity_stats()
    ctrl.switch_anilist_account("Bob")
    pending[0]()
    assert ctrl.watchlog.activity() == {}
    assert old_log.activity() == {}


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
    from src.core.controller import Controller

    season, year = Controller._current_season()
    assert payload["season"] == f"{season} {year}"
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
    assert snap["version"] == __version__

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
