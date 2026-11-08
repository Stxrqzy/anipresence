import hashlib

import pytest

from src.updates import download_release, parse_release, version_tuple


def test_github_release_selects_matching_installer_and_checksum():
    sha = 'a' * 64
    result = parse_release({'tag_name': 'v2.4.0', 'assets': [
        {'name': 'source.zip', 'browser_download_url': 'https://example.com/source.zip'},
        {'name': 'AniPresence-Setup-2.4.0.exe', 'browser_download_url': 'https://example.com/setup.exe',
         'digest': 'sha256:' + sha},
    ]})
    assert result == {'version': '2.4.0', 'url': 'https://example.com/setup.exe', 'sha256': sha}
    assert version_tuple('2.10.0') > version_tuple('2.9.9')


@pytest.mark.parametrize('payload', [
    {'version': '../bad'}, {'version': '2.4.0', 'url': 'file:///setup.exe'},
    {'version': '2.4.0', 'url': 'http://example.com/setup.exe'},
    {'version': '2.4.0', 'sha256': 'invalid'},
    {'tag_name': 'v2.4.0', 'prerelease': True},
])
def test_bad_release_is_rejected(payload):
    with pytest.raises(ValueError):
        parse_release(payload)


def test_download_is_atomic_and_checks_digest(tmp_path, monkeypatch):
    data = b'MZ' + b'installer payload' * 100
    class Response:
        url = 'https://example.com/setup.exe'
        headers = {'Content-Length': str(len(data))}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
        def iter_content(self, size): yield data
    monkeypatch.setattr('src.updates.requests.get', lambda *args, **kw: Response())
    release = {'version': '2.4.0', 'url': Response.url, 'sha256': hashlib.sha256(data).hexdigest()}
    progress = []
    dest = download_release(release, tmp_path, progress.append)
    assert dest.read_bytes() == data
    assert progress[-1] == 100
    release['sha256'] = 'a' * 64
    with pytest.raises(ValueError, match='checksum'):
        download_release(release, tmp_path)
    assert dest.read_bytes() == data  # a bad download cannot replace the valid installer
    assert not list(tmp_path.glob('*.part'))


def test_download_rejects_html_and_removes_partial_file(tmp_path, monkeypatch):
    class Response:
        url = 'https://example.com/setup.exe'
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
        def iter_content(self, size): yield b'<html>Sign in</html>'
    monkeypatch.setattr('src.updates.requests.get', lambda *args, **kw: Response())
    with pytest.raises(ValueError, match='not a Windows installer'):
        download_release({'version': '2.4.0', 'url': Response.url}, tmp_path)
    assert not list(tmp_path.iterdir())
