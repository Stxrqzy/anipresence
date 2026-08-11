"""Account avatar indicator and broadcast routing through the controller."""
import os
import sys

import pytest
from PySide6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.config import ConfigManager
from src.core.controller import Controller
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
    return Controller(cfg, UiBridge())


class _SigRecord:
    def __init__(self):
        self.events = []

    def emit(self, *args):
        self.events.append(args)


class _SentinelBridge:
    """Shallow UiBridge stand-in that records emitted (username, url) pairs."""

    def __init__(self):
        self.account_avatar = _SigRecord()
        self.log = _SigRecord()


def test_fetch_account_avatar_emits_url(tmp_path, app, monkeypatch):
    ctrl = make_controller(tmp_path)
    emitter = _SentinelBridge()
    ctrl.bridge = emitter
    ctrl.add_anilist_account("Alice")
    monkeypatch.setattr(ctrl.anilist, "fetch_user_profile", lambda name: "https://img/a.png")
    ctrl.fetch_account_avatar("Alice")
    # Thread completes quickly; poll briefly for the async emit.
    for _ in range(100):
        if emitter.account_avatar.events:
            break
        import time
        time.sleep(0.01)
    assert emitter.account_avatar.events == [("Alice", "https://img/a.png")]


def test_fetch_account_avatar_default_empty(tmp_path, app, monkeypatch):
    ctrl = make_controller(tmp_path)
    emitter = _SentinelBridge()
    ctrl.bridge = emitter
    ctrl.fetch_account_avatar("")
    assert emitter.account_avatar.events == [("", "")]


def test_fetch_account_avatar_emits_empty_on_failure(tmp_path, app, monkeypatch):
    ctrl = make_controller(tmp_path)
    emitter = _SentinelBridge()
    ctrl.bridge = emitter
    ctrl.add_anilist_account("Bob")

    def boom(name):
        raise RuntimeError("network down")

    monkeypatch.setattr(ctrl.anilist, "fetch_user_profile", boom)
    ctrl.fetch_account_avatar("Bob")
    for _ in range(100):
        if emitter.account_avatar.events:
            break
        import time
        time.sleep(0.01)
    assert emitter.account_avatar.events == [("Bob", "")]


def test_broadcast_default_discord(monkeypatch):
    from src.discord.vencord import VencordBridge
    import io
    import logging

    monkeypatch.setattr("requests.post", lambda *a, **k: _ok())
    vencord = VencordBridge()
    assert vencord.send({"x": 1}, "sig") is True


def _ok():
    class R:
        ok = True
    return R()