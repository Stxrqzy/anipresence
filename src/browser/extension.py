"""Localhost bridge for the AniPresence browser extension.

The extension reads the player state from any normal browser tab (no CDP debug
port needed) and POSTs it here as JSON. Bounded to 127.0.0.1 and gated by a
per-install token so a random website can't forge your presence through the
page's own fetch().

Not thread-safety-critical: only the bridge's internal dict is shared between
the HTTP handler threads and the controller poll loop, and it is guarded by a
lock. The server thread is a daemon so a late hang can't block shutdown.
"""
import json
import logging
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger("anipresence.extension")

# A single report window for a tab stops being "live" after this many seconds.
# Longer than the extension's 30s heartbeat so a hidden/background tab (whose
# setInterval Chrome throttles to ~1/min) keeps its entry without flapping.
TAB_TTL = 45.0

# Self-contained Now Playing page. No external assets: it polls /now.json
# every 5 seconds and re-renders. Exposing the bridge through a tunnel (e.g.
# cloudflared) makes this URL shareable with anyone.
NOW_PAGE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Now Playing · AniPresence</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  /* The .cover / .placeholder rules below set an explicit `display`, which
     overrides the UA's [hidden]{display:none} - so the hidden attribute alone
     would leave BOTH boxes visible. Force it here. */
  [hidden] { display: none !important; }
  body { margin: 0; min-height: 100vh; display: flex; align-items: center;
         justify-content: center; background: #0e1013; color: #e6e8eb;
         font-family: -apple-system, "Segoe UI", Roboto, Arial, sans-serif; }
  .card { background: #16181d; border: 1px solid #262a31; border-radius: 16px;
          padding: 28px; width: min(520px, 92vw); box-shadow: 0 20px 60px rgba(0,0,0,.5); }
  .cover { width: 100%; aspect-ratio: 3/4; object-fit: cover; border-radius: 12px;
           background: #23262c; display: block; }
  .placeholder { display: flex; align-items: center; justify-content: center;
           width: 100%; aspect-ratio: 3/4; border-radius: 12px; background: #23262c;
           color: #8a8f98; font-size: 40px; }
  h1 { margin: 18px 0 4px; font-size: 22px; line-height: 1.25; }
  .sub { color: #8a8f98; font-size: 14px; margin: 0 0 14px; }
  .bar { height: 8px; border-radius: 4px; background: #262a31; overflow: hidden; margin: 12px 0 6px; }
  .fill { height: 100%; background: #7c5cff; border-radius: 4px; width: 0; transition: width .4s; }
  .times { display: flex; justify-content: space-between; color: #8a8f98; font-size: 12px; }
  .status { display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: 12px;
            text-transform: uppercase; letter-spacing: .08em; }
  .playing { background: #1d4f2b; color: #6ee78a; }
  .paused  { background: #4a3a12; color: #ecc65a; }
  .idle    { background: #2a2e35; color: #9aa0a8; }
  a { color: #a08cff; text-decoration: none; }
  .foot { margin-top: 18px; color: #5f646d; font-size: 11px; text-align: center; }
</style>
</head>
<body>
  <div class="card" id="card">
    <img id="cover" class="cover" alt="cover" hidden>
    <div class="placeholder" id="ph">?</div>
    <h1 id="title">No anime detected</h1>
    <p class="sub" id="sub"></p>
    <span class="status idle" id="status">idle</span>
    <div class="bar"><div class="fill" id="fill"></div></div>
    <div class="times"><span id="cur">00:00</span><span id="total">00:00</span></div>
    <p class="foot" id="foot">AniPresence · <a id="link" href="#" hidden>Open the page</a></p>
  </div>
<script>
  function fmt(s) { s = Math.max(0, s|0); return String(Math.floor(s/60)).padStart(2,'0') + ':' +
                    String(s%60).padStart(2,'0'); }
  async function tick() {
    try {
      const r = await fetch('/now.json', { cache: 'no-store' });
      const d = await r.json();
      if (!d || !d.active) { render({ active:false }); return; }
      render(d);
    } catch (e) { render({ active:false }); }
  }
  function render(d) {
    const title = document.getElementById('title');
    const sub = document.getElementById('sub');
    const status = document.getElementById('status');
    const cover = document.getElementById('cover');
    const ph = document.getElementById('ph');
    const fill = document.getElementById('fill');
    const cur = document.getElementById('cur');
    const total = document.getElementById('total');
    const link = document.getElementById('link');
    if (!d.active) {
      title.textContent = 'Not watching right now';
      sub.textContent = 'The owner of this link has AniPresence running.';
      status.className = 'status idle'; status.textContent = 'idle';
      cover.hidden = true; ph.hidden = false;
      fill.style.width = '0%'; cur.textContent = '00:00'; total.textContent = '00:00';
      link.hidden = true; return;
    }
    document.title = 'Now Playing · ' + d.title;
    title.textContent = d.title || 'Watching something';
    sub.textContent = [d.episode ? 'Episode ' + d.episode : '', d.site].filter(Boolean).join(' · ');
    status.className = 'status ' + (d.status === 'playing' ? 'playing' : 'paused');
    status.textContent = d.status === 'playing' ? 'playing' : 'paused';
    const dur = d.duration || 0, pos = d.position || 0;
    if (d.cover) { cover.src = d.cover; cover.hidden = false; ph.hidden = true; }
    else { cover.hidden = true; ph.hidden = false; }
    fill.style.width = (dur > 0 ? Math.min(100, pos / dur * 100) : 0) + '%';
    cur.textContent = fmt(pos); total.textContent = dur > 0 ? fmt(dur) : '??:??';
    if (d.url) { link.href = d.url; link.textContent = 'Open the page'; link.hidden = false; }
    else link.hidden = true;
  }
  tick(); setInterval(tick, 5000);
</script>
</body>
</html>
"""


class _Handler(BaseHTTPRequestHandler):  # noqa: N801 (underscore-prefixed helper)
    server_version = "AniPresence/1.1"

    def log_message(self, *args):  # keep the repeated poll traffic out of logs
        pass

    # ---------------------------------------------------------------- helpers
    @property
    def bridge(self):
        return self.server.bridge

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-AniPresence-Token")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        # Chrome's Private Network Access spec: a preflight to 127.0.0.1 must
        # be answered with this header or the real request is blocked.
        self.send_header("Access-Control-Allow-Private-Network", "true")

    # ------------------------------------------------------------- http verbs
    def do_OPTIONS(self):  # noqa: N802 (BaseHTTPRequestHandler naming)
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_GET(self):  # noqa: N802 (BaseHTTPRequestHandler naming)
        path = self.path.rstrip("/").split("?", 1)[0]
        if path == "":
            # A bare tunnel hostname (e.g. the quick-tunnel link people type
            # after copying) lands here; point it at the Now Playing page.
            self.send_response(302)
            self.send_header("Location", "/now")
            self.end_headers()
            return
        if path == "/ping":
            self._authorize_and_reply(empty=True)
            return
        if path == "/now.json":
            self._serve_now_json()
            return
        if path == "/now":
            self._serve_now_page()
            return
        self.send_response(404)
        self.end_headers()

    def _now_snapshot(self):
        """JSON-ready now-playing snapshot (or None when the provider is missing)."""
        provider = getattr(self.bridge, "now_provider", None)
        if not callable(provider):
            return None
        try:
            return provider()
        except Exception:  # noqa: BLE001 - never let a bad snapshot kill the page
            return None

    def _serve_now_json(self):
        """Public GET /now.json: the current presence as JSON (no token needed)."""
        snapshot = self._now_snapshot() or {}
        body = json.dumps(snapshot).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _serve_now_page(self):
        """Public GET /now: a self-contained page polling /now.json every 5s."""
        body = NOW_PAGE_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802 (BaseHTTPRequestHandler naming)
        if self.path.rstrip("/") == "/ping":
            self._authorize_and_reply(empty=True)
            return
        if self.path.rstrip("/") != "/report":
            self.send_response(404)
            self.end_headers()
            return

        # optional CORS pre-flight is handled above; a plain POST with the
        # content-type header is what the extension actually sends.
        if self.headers.get("X-AniPresence-Token", "") != self.bridge.token:
            self.bridge.note_rejected(self.client_address[0])
            self.send_response(403)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 1_000_000:
            self.send_response(400)
            self.end_headers()
            return
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8", "replace"))
        except (ValueError, UnicodeDecodeError):
            self.send_response(400)
            self.end_headers()
            return

        self.bridge.report(body)
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    def _authorize_and_reply(self, empty=True):
        if self.headers.get("X-AniPresence-Token", "") != self.bridge.token:
            self.bridge.note_rejected(self.client_address[0])
            self.send_response(403)
            self.end_headers()
            return
        self.bridge.note_activity()
        body = json.dumps({"ok": True}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)


class ExtensionBridge:
    """Keeps the latest per-tab playback state sent by the browser extension."""

    def __init__(self, port=8793, token=""):
        # Keep 0 usable so tests can bind an OS-assigned ephemeral port
        # (0 or "" mean "unset" only in the config sense, not here).
        self._port = int(port) if port not in (None, "") else 8793
        self._token = token or ""
        self._lock = threading.Lock()
        self._tabs = {}       # tab_key -> {"at": float, "payload": dict}
        self._server = None
        self._thread = None
        self._notified_activity = False
        self._notified_reject = 0.0
        self._last_report = 0.0
        self._extension_version = ""
        # Set by the controller so GET /now.json can serve a live snapshot.
        self.now_provider = None

    @property
    def port(self):
        return self._port

    @property
    def bound_port(self):
        """Configured port, or the actual OS-assigned one when it was 0."""
        if self._server is not None:
            return self._server.server_address[1]
        return self._port

    @property
    def token(self):
        return self._token

    @property
    def running(self):
        return self._server is not None

    # ---------------------------------------------------------------- server
    def start(self):
        if self._server is not None:
            return
        try:
            server = ThreadingHTTPServer(("127.0.0.1", self._port), _Handler)
        except OSError as exc:
            logger.warning("extension bridge could not bind 127.0.0.1:%s (%s)", self._port, exc)
            return
        server.bridge = self
        self._server = server
        self._thread = threading.Thread(
            target=server.serve_forever, name="extension-bridge", daemon=True
        )
        self._thread.start()

    def stop(self):
        server = self._server
        self._server = None
        self._thread = None
        if server is not None:
            server.shutdown()

    # ---------------------------------------------------------------- ingest
    def report(self, body):
        """Accept one payload object or a batched ``{"tabs": [...]}`` body."""
        self.note_activity()
        tabs = body.get("tabs") if isinstance(body, dict) else None
        if not isinstance(tabs, list):
            tabs = [body]
        now = time.time()
        with self._lock:
            self._last_report = now
            self._extension_version = str(body.get("extensionVersion") or "")[:32] if isinstance(body, dict) else ""
            for item in tabs:
                if not isinstance(item, dict):
                    continue
                # The extension normally sends its own `tab` key; fall back to the
                # URL so a malformed peer can't silently drop reports.
                key = item.get("tab") or item.get("url")
                if not key:
                    continue
                self._tabs[str(key)] = {"at": now, "payload": item}

    def live_tabs(self, ttl=TAB_TTL):
        """Return ``[(tab_key, payload), ...]`` fresh enough to trust."""
        now = time.time()
        fresh = []
        with self._lock:
            for key, entry in list(self._tabs.items()):
                if now - entry["at"] <= ttl:
                    fresh.append((key, entry["payload"]))
                else:
                    self._tabs.pop(key, None)
        return fresh

    def prune_all(self):
        with self._lock:
            self._tabs.clear()

    # ------------------------------------------------------------- diagnostics
    def diagnostics(self):
        """Local bridge health; never exposes the bridge token or page URLs."""
        now = time.time()
        with self._lock:
            readers = sorted({str(e['payload'].get('readerVersion') or 'unknown')[:32]
                              for e in self._tabs.values() if now - e['at'] <= TAB_TTL})
            return {
                'running': self.running, 'port': self.bound_port,
                'connected': bool(self._last_report and now - self._last_report <= 90),
                'last_report': self._last_report, 'version': self._extension_version,
                'expected': '1.7.0', 'readers': readers,
                'rejected': self._notified_reject > self._last_report,
            }

    def note_activity(self):
        """Log once per run when the extension successfully connects."""
        if self._notified_activity:
            return
        self._notified_activity = True
        logger.info("browser extension connected (token accepted)")

    def note_rejected(self, peer):
        """Throttled log when a request arrives with a wrong/missing token."""
        now = time.time()
        if now - self._notified_reject < 30:
            return
        self._notified_reject = now
        logger.warning(
            "extension bridge rejected a request from %s (wrong token). Reload the "
            "extension so it re-reads settings.json, or check its Options page.",
            peer,
        )


def ensure_token(raw_token):
    """Return a non-empty token, generating and persisting one when empty."""
    token = (raw_token or "").strip()
    if not token:
        token = secrets.token_hex(8)
        logger.info("generated a new extension bridge token: %s", token)
    return token
