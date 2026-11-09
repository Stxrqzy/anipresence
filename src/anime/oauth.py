"""AniList browser login with authorization-code and implicit grant support.

Register REDIRECT_URI on the AniList developer page. A per-user client secret
enables the code grant; it is held for the login attempt only, never bundled.
Callback parameters are not logged and are removed from browser history.
"""
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlencode

import requests

from .anilist import AniListClient

REDIRECT_URI = "http://127.0.0.1:8794/callback"
CALLBACK_PAGE = b"""<!doctype html><meta charset="utf-8"><title>AniPresence login</title>
<body style="font:18px system-ui;background:#16181d;color:#eee;padding:48px">
<h1>AniPresence</h1><p id="status">Completing AniList login...</p>
<script>
const query = new URLSearchParams(location.search);
const params = query.has('code') || query.has('error')
  ? query : new URLSearchParams(location.hash.slice(1));
history.replaceState(null, '', '/callback');
fetch('/callback', {method:'POST', headers:{'Content-Type':'application/json'},
body:JSON.stringify({token:params.get('access_token'), code:params.get('code'), state:params.get('state'),
error:params.get('error')})}).then(r => {
document.getElementById('status').textContent = r.ok
? 'Authorization received. Return to AniPresence to finish connecting.'
: 'Login was not accepted. Start again from AniPresence.';
}).catch(() => document.getElementById('status').textContent =
'AniPresence is no longer waiting. Start login again in the app.');
</script>"""


class OAuthLogin:
    def __init__(self, client_id, complete, port=8794, timeout=180, client_secret=""):
        if not str(client_id).strip().isdigit():
            raise ValueError("Set the AniList OAuth client ID in Settings first.")
        self.state = secrets.token_urlsafe(32)
        self.client_id = str(client_id).strip()
        self._client_secret = (client_secret or '').strip()
        self.response_type = 'code' if self._client_secret else 'token'
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
                if self.path == f'/start/{owner.state}':
                    # Let the browser handle the OAuth query string, rather than
                    # passing ampersands through Windows URL-handler commands.
                    self.send_response(302)
                    self.send_header('Location', owner.url)
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('Referrer-Policy', 'no-referrer')
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
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
                    credential = data.get(owner.response_type)
                    if not data.get('error') and (not isinstance(credential, str) or not credential.strip()):
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
        self.redirect_uri = f'http://127.0.0.1:{self.server.server_port}/callback'
        self.url = 'https://anilist.co/api/v2/oauth/authorize?' + urlencode({
            'client_id': self.client_id, 'response_type': self.response_type,
            'redirect_uri': self.redirect_uri,
            'state': self.state,
        })
        self.launch_url = f'http://127.0.0.1:{self.server.server_port}/start/{self.state}'

    def start(self):
        threading.Thread(target=self._run, daemon=True, name='anilist-login').start()

    def cancel(self):
        self.cancelled.set()

    def _run(self):
        try:
            result = self._authenticate()
        except Exception:
            result = {'message': 'AniList login failed. Start a new login attempt.'}
        finally:
            self._client_secret = ''
            self.result = None
        if result is not None and not self.cancelled.is_set():
            self.complete(result)

    def _authenticate(self):
        deadline = time.monotonic() + self.timeout
        try:
            while not self.cancelled.is_set() and self.result is None and time.monotonic() < deadline:
                self.server.handle_request()
        finally:
            self.server.server_close()
        if self.cancelled.is_set():
            return
        if self.result is None:
            return {'message': 'AniList login timed out. Try connecting again.'}
        if self.result.get('error'):
            return {'message': 'AniList authorization was declined.'}
        try:
            token = self._exchange_code(self.result['code']) if self.response_type == 'code' else self.result['token']
        except ValueError as exc:
            return {'message': str(exc)}
        if self.cancelled.is_set():
            return
        client = AniListClient(access_token=token)
        try:
            viewer = client.fetch_viewer()
            result = {'token': token, 'account': viewer['name'], 'message': ''}
        except Exception:
            result = {'message': 'Could not verify the AniList account. Please try again.'}
        finally:
            client._session.close()
        return result

    def _exchange_code(self, code):
        """Exchange with AniList directly; do not expose response bodies/secrets."""
        try:
            with requests.post('https://anilist.co/api/v2/oauth/token', data={
                'grant_type': 'authorization_code', 'client_id': self.client_id,
                'client_secret': self._client_secret, 'redirect_uri': self.redirect_uri,
                'code': code,
            }, headers={'Accept': 'application/json'}, timeout=(10, 20), allow_redirects=False) as response:
                try:
                    data = response.json()
                except ValueError:
                    raise ValueError('AniList returned an invalid token response. Try again.') from None
                if not isinstance(data, dict):
                    raise ValueError('AniList returned an invalid token response. Try again.')
                if response.status_code != 200 or data.get('error'):
                    error = data.get('error')
                    if error == 'invalid_client':
                        raise ValueError('AniList rejected the client credentials. Check the Client ID and Client Secret.')
                    if error == 'invalid_grant':
                        raise ValueError('The authorization code expired or the redirect URL differs. Start login again.')
                    raise ValueError('AniList rejected the code exchange. Check the client credentials and redirect URL.')
                token = data.get('access_token')
                if not isinstance(token, str) or not token.strip():
                    raise ValueError('AniList did not return an access token. Start login again.')
                return token
        except requests.RequestException:
            raise ValueError('Could not reach AniList to exchange the code. Start login again.') from None
