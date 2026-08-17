"""Extension bridge: token generation, in-memory tab store and HTTP serving."""
import http.client
import json

from src.browser.extension import ExtensionBridge, ensure_token


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now


def test_ensure_token_generates_and_preserves():
    empty = ensure_token("")
    assert len(empty) == 16
    assert ensure_token("abc") == "abc"
    assert ensure_token("  padded  ") == "padded"


def test_report_and_live_tabs_freshness(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr("src.browser.extension.time.time", clock.time)
    bridge = ExtensionBridge(port=8793, token="tok")
    bridge.report({"tab": "t1", "url": "https://a.example/"})

    tabs = bridge.live_tabs()
    assert len(tabs) == 1
    assert tabs[0][0] == "t1"

    clock.now += 46.0  # past the 45s TTL
    assert bridge.live_tabs() == []
    assert bridge.live_tabs() == []  # pruned entry stays gone


def test_reports_batched_tabs(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr("src.browser.extension.time.time", clock.time)
    bridge = ExtensionBridge()
    bridge.report({"tabs": [{"tab": "a", "url": "u1"}, {"tab": "b", "url": "u2"}]})
    assert sorted(k for k, _ in bridge.live_tabs()) == ["a", "b"]


def test_report_without_tab_falls_back_to_url(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr("src.browser.extension.time.time", clock.time)
    bridge = ExtensionBridge()
    # Background worker previously forwarded payloads without a `tab` key; the
    # bridge must not silently drop them.
    bridge.report({"tab": None, "url": "https://a.example/x"})
    tabs = bridge.live_tabs()
    assert len(tabs) == 1
    assert tabs[0][0] == "https://a.example/x"


def test_report_ignores_items_without_any_key(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr("src.browser.extension.time.time", clock.time)
    bridge = ExtensionBridge()
    bridge.report({"tabs": [{"foo": "bar"}]})
    assert bridge.live_tabs() == []


def test_prune_all():
    bridge = ExtensionBridge()
    bridge.report({"tab": "t1", "url": "u"})
    bridge.prune_all()
    assert bridge.live_tabs() == []


# ------------------------------------------------------------------- HTTP
def _start_bridge(token="secret"):
    bridge = ExtensionBridge(port=0, token=token)
    bridge.start()
    assert bridge.running
    return bridge, bridge.bound_port


def _post(port, path, body, token=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["X-AniPresence-Token"] = token
    conn.request("POST", path, body=json.dumps(body), headers=headers)
    return conn.getresponse()


def test_unauthorized_post_rejected():
    bridge, port = _start_bridge(token="secret")
    try:
        resp = _post(port, "/report", {"tab": "t1", "url": "u"}, token="wrong")
        assert resp.status == 403
        resp.close()
        assert bridge.live_tabs() == []

        resp = _post(port, "/report", {"tab": "t1", "url": "u"}, token=None)
        assert resp.status == 403
        resp.close()
    finally:
        bridge.stop()


def test_authorized_post_stored():
    bridge, port = _start_bridge(token="secret")
    try:
        resp = _post(port, "/report", {"tab": "t1", "url": "https://ok.example/"}, token="secret")
        assert resp.status == 200
        resp.close()
        tabs = bridge.live_tabs()
        assert len(tabs) == 1
        assert tabs[0][1]["url"] == "https://ok.example/"
    finally:
        bridge.stop()


def test_unknown_path_404():
    bridge, port = _start_bridge(token="secret")
    try:
        resp = _post(port, "/nope", {"tab": "t1"}, token="secret")
        assert resp.status == 404
        resp.close()
    finally:
        bridge.stop()


def test_root_redirects_to_now_page():
    """A bare tunnel hostname (GET /) must land on the Now Playing page."""
    bridge, port = _start_bridge(token="secret")
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/")
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 302
        assert resp.getheader("Location") == "/now"
    finally:
        bridge.stop()


def test_options_cors_headers():
    bridge, port = _start_bridge(token="secret")
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("OPTIONS", "/report")
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 204
        assert resp.getheader("Access-Control-Allow-Origin") == "*"
        assert resp.getheader("Access-Control-Allow-Headers") is not None
        assert resp.getheader("Access-Control-Allow-Private-Network") == "true"
    finally:
        bridge.stop()


def test_ping_endpoint():
    bridge, port = _start_bridge(token="secret")
    try:
        wrong = _post(port, "/ping", {"ping": True}, token="nope")
        assert wrong.status == 403
        wrong.close()

        ok = _post(port, "/ping", {"ping": True}, token="secret")
        assert ok.status == 200
        assert json.loads(ok.read().decode()) == {"ok": True}
        ok.close()
    finally:
        bridge.stop()