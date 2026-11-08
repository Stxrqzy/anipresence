"""Browser-to-loopback OAuth: validate state/origin and verify the token owner."""
import http.client
import json
import threading
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from src.anime.oauth import OAuthLogin


def post(flow, body, origin=None):
    port = flow.server.server_port
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    try:
        conn.request('POST', '/callback', json.dumps(body), {
            'Content-Type': 'application/json',
            'Origin': origin or f'http://127.0.0.1:{port}',
        })
        response = conn.getresponse()
        response.read()
        return response.status
    finally:
        conn.close()


def test_browser_login_validates_state_and_identifies_account(monkeypatch):
    result, seen = [], []
    done = threading.Event()
    def client(access_token):
        seen.append(access_token)
        return SimpleNamespace(fetch_viewer=lambda: {'id': 1, 'name': 'Alice'},
                               _session=SimpleNamespace(close=lambda: None))
    monkeypatch.setattr('src.anime.oauth.AniListClient', client)
    flow = OAuthLogin('123', lambda data: (result.append(data), done.set()), port=0)
    query = parse_qs(urlsplit(flow.url).query)
    assert query['client_id'] == ['123']
    assert query['response_type'] == ['token']
    assert query['state'] == [flow.state]
    assert 'client_secret' not in query
    flow.start()
    try:
        assert post(flow, {'state': 'wrong', 'token': 'secret'}) == 403
        assert post(flow, {'state': flow.state, 'token': 'secret'}, 'https://other.example') == 403
        assert not seen
        assert post(flow, {'state': flow.state, 'token': 'secret'}) == 200
        assert done.wait(5)
        assert result == [{'token': 'secret', 'account': 'Alice', 'message': ''}]
        assert seen == ['secret']
    finally:
        flow.cancel()


def test_login_denied_and_timeout_do_not_save_tokens():
    for declined in (True, False):
        result = []
        done = threading.Event()
        flow = OAuthLogin('123', lambda data: (result.append(data), done.set()),
                          port=0, timeout=2 if declined else 0)
        flow.start()
        try:
            if declined:
                assert post(flow, {'state': flow.state, 'error': 'access_denied'}) == 200
            assert done.wait(5)
            assert 'token' not in result[0]
            assert ('declined' if declined else 'timed out') in result[0]['message']
        finally:
            flow.cancel()
