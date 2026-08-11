"""Vencord plugin bridge: local POST of activity payloads, silently offline."""
import requests

from src.discord.vencord import VencordBridge


def _ok_response():
    class R:
        ok = True
    return R()


def _fail_response():
    class R:
        ok = False
    return R()


class _ReqError(Exception):
    pass


def test_send_posts_to_plugin_url(monkeypatch):
    calls = {}

    def fake_post(url, json, timeout):
        calls["url"] = url
        calls["json"] = json
        calls["timeout"] = timeout
        return _ok_response()

    monkeypatch.setattr("requests.post", fake_post)
    bridge = VencordBridge(url="http://127.0.0.1:6339/activity", timeout=1.5)
    ok = bridge.send({"details": "Watching: X"}, "sig123")

    assert ok is True
    assert calls["url"] == "http://127.0.0.1:6339/activity"
    assert calls["json"] == {"activity": {"details": "Watching: X"}, "sig": "sig123"}
    assert calls["timeout"] == 1.5


def test_send_false_when_not_ok(monkeypatch):
    monkeypatch.setattr("requests.post", lambda url, json, timeout: _fail_response())
    assert VencordBridge().send({}, None) is False


def test_send_false_when_plugin_down(monkeypatch):
    def boom(url, json, timeout):
        raise requests.exceptions.ConnectionError("connection refused")

    monkeypatch.setattr("requests.post", boom)
    assert VencordBridge(timeout=0.2).send({}, None) is False


def test_clear_sends_clear_flag(monkeypatch):
    calls = {}

    def fake_post(url, json, timeout):
        calls["json"] = json
        return _ok_response()

    monkeypatch.setattr("requests.post", fake_post)
    VencordBridge().clear()
    assert calls["json"] == {"clear": True}


def test_source_layout():
    """Regression guard for the recreated module layout."""
    text = open("src/discord/vencord.py", encoding="utf-8").read()
    assert 'logger.debug("vencord bridge send failed: %s", exc)' in text