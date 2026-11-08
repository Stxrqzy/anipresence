"""AniList's desktop-friendly implicit grant, received on a loopback callback.

Register REDIRECT_URI on the AniList developer page; only the public client ID
is required. Tokens never appear in a request URL or HTTP access log.
"""
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlencode

from .anilist import AniListClient

REDIRECT_URI = "http://127.0.0.1:8794/callback"
CALLBACK_PAGE = b"""<!doctype html><meta charset="utf-8"><title>AniPresence login</title>
<body style="font:18px system-ui;background:#16181d;color:#eee;padding:48px">
<h1>AniPresence</h1><p id="status">Completing AniList login...</p>
<script>
const params = new URLSearchParams(location.hash.slice(1));
history.replaceState(null, '', '/callback');
fetch('/callback', {method:'POST', headers:{'Content-Type':'application/json'},
body:JSON.stringify({token:params.get('access_token'), state:params.get('state'),
error:params.get('error')})}).then(r => {
document.getElementById('status').textContent = r.ok
? 'Authorization received. Return to AniPresence to finish connecting.'
: 'Login was not accepted. Start again from AniPresence.';
}).catch(() => document.getElementById('status').textContent =
'AniPresence is no longer waiting. Start login again in the app.');
</script>"""


class OAuthLogin:
    def __init__(self, client_id, complete, port=8794, timeout=180):
        if not str(client_id).strip().isdigit():
            raise ValueError("Set the AniList OAuth client ID in Settings first.")
        self.state = secrets.token_urlsafe(32)
        self.cancelled = threading.Event()
        self.result = None
        self.result_lock = threading.Lock()
        self.complete = complete
        self.timeout = timeout
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(5)

            def log_message(self, *args):
                pass

            def do_GET(self):  # noqa: N802
                if self.path.split('?')[0] != '/callback':
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('Content-Length', str(len(CALLBACK_PAGE)))
                self.end_headers()
                self.wfile.write(CALLBACK_PAGE)

            def do_POST(self):  # noqa: N802
                origin = f'http://127.0.0.1:{self.server.server_port}'
                if self.path != '/callback' or self.headers.get('Origin') != origin:
                    self.send_error(403)
                    return
                try:
                    length = int(self.headers.get('Content-Length', 0))
                    if not 0 < length <= 16384:
                        raise ValueError()
                    data = json.loads(self.rfile.read(length))
                    if not isinstance(data, dict):
                        raise ValueError()
                    if not secrets.compare_digest(str(data.get('state') or ''), owner.state):
                        self.send_error(403)
                        return
                    token = data.get('token')
                    if not data.get('error') and (not isinstance(token, str) or not token.strip()):
                        raise ValueError()
                except (ValueError, TypeError):
                    self.send_error(400)
                    return
                with owner.result_lock:
                    if owner.cancelled.is_set() or owner.result is not None:
                        self.send_error(409)
                        return
                    owner.result = data
                self.send_response(200)
                self.send_header('Content-Length', '0')
                self.end_headers()

        self.server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        self.server.timeout = 0.25
        self.url = 'https://anilist.co/api/v2/oauth/authorize?' + urlencode({
            'client_id': str(client_id).strip(), 'response_type': 'token',
            'redirect_uri': f'http://127.0.0.1:{self.server.server_port}/callback',
            'state': self.state,
        })

    def start(self):
        threading.Thread(target=self._run, daemon=True, name='anilist-login').start()

    def cancel(self):
        self.cancelled.set()

    def _run(self):
        deadline = time.monotonic() + self.timeout
        try:
            while not self.cancelled.is_set() and self.result is None and time.monotonic() < deadline:
                self.server.handle_request()
        finally:
            self.server.server_close()
        if self.cancelled.is_set():
            return
        if self.result is None:
            self.complete({'message': 'AniList login timed out. Try connecting again.'})
            return
        if self.result.get('error'):
            self.complete({'message': 'AniList authorization was declined.'})
            return
        client = AniListClient(access_token=self.result['token'])
        try:
            viewer = client.fetch_viewer()
            result = {'token': self.result['token'], 'account': viewer['name'], 'message': ''}
        except Exception:
            result = {'message': 'Could not verify the AniList account. Please try again.'}
        finally:
            client._session.close()
        if not self.cancelled.is_set():
            self.complete(result)
