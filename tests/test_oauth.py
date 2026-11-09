"""Browser-to-loopback OAuth: validate state/origin and verify the token owner."""
import http.client
import json
import threading
import pytest
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from src.anime.oauth import CALLBACK_PAGE, OAuthLogin


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
        assert '&' not in flow.launch_url and '?' not in flow.launch_url
        conn = http.client.HTTPConnection('127.0.0.1', flow.server.server_port, timeout=5)
        try:
            conn.request('GET', urlsplit(flow.launch_url).path)
            response = conn.getresponse()
            assert response.status == 302
            redirected = urlsplit(response.getheader('Location'))
            assert redirected.netloc == 'anilist.co'
            assert parse_qs(redirected.query) == query
            response.read()
        finally:
            conn.close()
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


def test_code_grant_exchanges_code_and_clears_secret(monkeypatch):
    result, exchanges, verified = [], [], []
    done = threading.Event()
    class Response:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def json(self): return {'access_token': 'returned-access-token'}
    def exchange(url, **kwargs):
        exchanges.append((url, kwargs))
        return Response()
    def client(access_token):
        verified.append(access_token)
        return SimpleNamespace(fetch_viewer=lambda: {'id': 2, 'name': 'Alice'},
                               _session=SimpleNamespace(close=lambda: None))
    monkeypatch.setattr('src.anime.oauth.requests.post', exchange)
    monkeypatch.setattr('src.anime.oauth.AniListClient', client)
    flow = OAuthLogin('123', lambda data: (result.append(data), done.set()), port=0,
                      client_secret='user-owned-secret')
    query = parse_qs(urlsplit(flow.url).query)
    assert query['response_type'] == ['code']
    assert 'user-owned-secret' not in flow.url and 'client_secret' not in query
    flow.start()
    try:
        assert post(flow, {'state': 'wrong', 'code': 'one-use-code'}) == 403
        assert post(flow, {'state': flow.state, 'token': 'wrong-grant'}) == 400
        assert not exchanges
        assert post(flow, {'state': flow.state, 'code': 'one-use-code'}) == 200
        assert done.wait(5)
        url, request = exchanges[0]
        assert url == 'https://anilist.co/api/v2/oauth/token'
        assert request['data'] == {
            'grant_type': 'authorization_code', 'client_id': '123',
            'client_secret': 'user-owned-secret', 'redirect_uri': query['redirect_uri'][0],
            'code': 'one-use-code',
        }
        assert request['allow_redirects'] is False
        assert verified == ['returned-access-token']
        assert result == [{'token': 'returned-access-token', 'account': 'Alice', 'message': ''}]
        assert flow._client_secret == '' and flow.result is None
    finally:
        flow.cancel()


@pytest.mark.parametrize('error,expected', [
    ('invalid_client', 'client credentials'),
    ('invalid_grant', 'expired'),
    ('unsupported_grant_type', 'rejected the code exchange'),
])
def test_code_exchange_errors_do_not_expose_response_or_secret(monkeypatch, error, expected):
    class Response:
        status_code = 400
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def json(self): return {'error': error, 'message': 'secret-from-response'}
    monkeypatch.setattr('src.anime.oauth.requests.post', lambda *a, **kw: Response())
    flow = OAuthLogin('123', lambda data: None, port=0, client_secret='user-secret')
    try:
        with pytest.raises(ValueError, match=expected) as exc:
            flow._exchange_code('code')
        assert 'secret-from-response' not in str(exc.value)
        assert 'user-secret' not in str(exc.value)
    finally:
        flow.server.server_close()


def test_cancelled_code_login_discards_secret_without_callback():
    results = []
    flow = OAuthLogin('123', results.append, port=0, client_secret='user-secret')
    flow.cancel()
    flow._run()
    assert flow._client_secret == ''
    assert not results


def test_callback_page_reads_query_codes_and_fragment_tokens():
    import shutil
    import subprocess
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js required to exercise the browser callback')
    script = CALLBACK_PAGE.decode().split('<script>')[1].split('</script>')[0]
    driver = r"""
const vm = require('node:vm');
const script = require('node:fs').readFileSync(0, 'utf8');
const results = [];
for (const location of [
  {search:'?code=authorization-code&state=state-1',hash:''},
  {search:'',hash:'#access_token=access-token&state=state-2'},
  {search:'?error=access_denied&state=state-3',hash:''},
]) {
  const result = {};
  vm.runInNewContext(script, {
    URLSearchParams, location,
    history: {replaceState(a,b,url) {result.cleaned=url;}},
    fetch(url, options) {result.body=JSON.parse(options.body);return Promise.resolve({ok:true});},
    document: {getElementById() {return {};}}
  });
  results.push(result);
}
console.log(JSON.stringify(results));
"""
    process = subprocess.run([node, '-e', driver], input=script, text=True, capture_output=True)
    assert process.returncode == 0, process.stderr
    results = json.loads(process.stdout)
    assert results[0]['body'] == {'token': None, 'code': 'authorization-code', 'state': 'state-1', 'error': None}
    assert results[1]['body']['token'] == 'access-token'
    assert results[2]['body']['error'] == 'access_denied'
    assert all(item['cleaned'] == '/callback' for item in results)
