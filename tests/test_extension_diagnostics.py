import json
from pathlib import Path

from src.browser.extension import ExtensionBridge


def test_bridge_diagnostics_detects_stale_reader_and_worker(monkeypatch):
    clock = [1000]
    monkeypatch.setattr('src.browser.extension.time.time', lambda: clock[0])
    bridge = ExtensionBridge(token='do-not-expose')
    bridge.report({'extensionVersion': '1.7.0', 'tabs': [
        {'tab': '1', 'url': 'https://ani.pm/anime/private', 'readerVersion': '1.6.2'}]})
    d = bridge.diagnostics()
    assert d['connected']
    assert d['version'] == d['expected'] == '1.7.0'
    assert d['readers'] == ['1.6.2']
    assert 'do-not-expose' not in str(d) and 'private' not in str(d)
    bridge.report({'tabs': [{'tab': '1', 'url': 'url'}]})
    assert bridge.diagnostics()['version'] == ''
    assert bridge.diagnostics()['readers'] == ['unknown']
    clock[0] += 100
    assert not bridge.diagnostics()['connected']
    assert bridge.diagnostics()['readers'] == []


def test_expected_extension_version_matches_bundle():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root/'browser-extension/manifest.json').read_text())
    assert ExtensionBridge().diagnostics()['expected'] == manifest['version']
    assert f'READER_VERSION = "{manifest["version"]}"' in (root/'browser-extension/content.js').read_text()
