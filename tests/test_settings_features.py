"""Settings wiring and async callbacks survive a theme/UI rebuild."""
import os
import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication

from src.config import ConfigManager
from src.core.controller import Controller
from src.ui.app import MainWindow
from src.ui.bridge import UiBridge


@pytest.mark.parametrize('layout', ['modern', 'classic'])
def test_settings_controls_and_rebuild(tmp_path, monkeypatch, layout):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr('src.core.controller.APP_DIR', tmp_path)
    monkeypatch.setattr('src.ui.app.APP_DIR', tmp_path)
    monkeypatch.setattr(MainWindow, '_build_tray', lambda self: None)
    for method in ('refresh_library', 'refresh_calendar', 'fetch_account_avatar'):
        monkeypatch.setattr(Controller, method, lambda *a, **kw: None)
    cfg = ConfigManager(tmp_path/'config.json')
    cfg.set(auto_check_updates=False, chrome_auto_launch=False, ui_layout=layout)
    bridge = UiBridge()
    ctrl = Controller(cfg, bridge)
    window = MainWindow(cfg, bridge, ctrl)
    try:
        window.presence_options['presence_cover_art'].setChecked(False)
        assert not cfg.data.presence_cover_art
        window._on_anilist_login()  # setup guidance rather than opening an unregistered client
        assert 'client ID' in window.anilist_login_status.text()
        from types import SimpleNamespace
        seen = {}
        def fake_login(client_id, complete, **kwargs):
            seen.update(client_id=client_id, **kwargs)
            return SimpleNamespace(start=lambda: None, cancel=lambda: None,
                                   launch_url='http://127.0.0.1:8794/start/test')
        monkeypatch.setattr('src.anime.oauth.OAuthLogin', fake_login)
        monkeypatch.setattr('src.ui.app.webbrowser.open', lambda url: True)
        window.anilist_client_edit.setText('123')
        window.anilist_secret_edit.setText('user-owned-client-secret')
        window._on_anilist_login()
        assert seen == {'client_id': '123', 'client_secret': 'user-owned-client-secret'}
        assert window.anilist_secret_edit.text() == ''
        assert 'user-owned-client-secret' not in cfg.path.read_text()
        window._on_anilist_login()  # cancel the mocked in-flight login
        window._on_update_status({'stage': 'available', 'release': {'version': '9.0.0', 'url': 'https://example.com/setup.exe'},
                                  'message': 'New release'})
        assert window.update_download_btn.isEnabled()
        window.tabbar.setCurrentIndex(window._settings_tab_index)
        window._rebuild_ui()
        assert window.stack.currentWidget() is window._settings_page
        assert window.update_status_label.text() == 'New release'
        assert not window.presence_options['presence_cover_art'].isChecked()
        bridge.anilist_sync_status.emit({'account': 'Alice', 'last_success': 1000,
                                        'pending': ['Anime · Episode 2'], 'error': 'Offline', 'activity': 'Needs retry'})
        app.processEvents()
        assert 'Alice' in window.sync_details.text()
        assert 'Offline' in window.sync_details.text()
        # A cancelled login result must never replace the active credentials.
        cfg.set(anilist_access_token='current')
        window._on_anilist_login_result({'generation': -1, 'token': 'stale', 'account': 'Wrong'})
        assert cfg.data.anilist_access_token == 'current'
    finally:
        window._quitting = True
        window.close()
        window.deleteLater()
        app.processEvents()


def test_layout_switch_keeps_live_widgets_and_async_operations(tmp_path, monkeypatch):
    from types import SimpleNamespace
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr('src.core.controller.APP_DIR', tmp_path)
    monkeypatch.setattr('src.ui.app.APP_DIR', tmp_path)
    monkeypatch.setattr(MainWindow, '_build_tray', lambda self: None)
    for method in ('refresh_library', 'refresh_calendar', 'fetch_account_avatar'):
        monkeypatch.setattr(Controller, method, lambda *a, **kw: None)
    cfg = ConfigManager(tmp_path/'config.json')
    cfg.set(auto_check_updates=False, chrome_auto_launch=False)
    bridge = UiBridge()
    window = MainWindow(cfg, bridge, Controller(cfg, bridge))
    flow = SimpleNamespace(cancel=lambda: None)
    try:
        window._login_flow = flow
        window._login_generation = 7
        window.anilist_secret_edit.setText('unsaved-secret')
        window.streaming_url.setText('https://example.com/search?q={query}')
        window.log_view.setPlainText('Previous activity')
        bridge.detection.emit({'title': 'Live anime', 'episode': 4, 'duration': 1000,
                               'current_time': 520, 'url': 'https://example.com/episode-4'})
        window._on_update_status({'stage': 'downloading', 'message': 'Downloading installer'})
        cover, token = window.cover_label, window.anilist_token_edit
        window.tabbar.setCurrentIndex(window._settings_tab_index)
        for modern in (False, True, False, True):
            window.modern_ui_check.setChecked(modern)
            app.processEvents()
            expected = 'modern' if modern else 'classic'
            assert ConfigManager(cfg.path).data.ui_layout == expected
            assert window.tabbar.currentIndex() == window._settings_tab_index
            assert window.stack.currentWidget() is window._settings_page
            assert window._nav_buttons[window._settings_tab_index].isChecked()
            assert window.cover_label is cover and window.anilist_token_edit is token
            assert window._login_flow is flow and window._login_generation == 7
            assert window.anilist_secret_edit.text() == 'unsaved-secret'
            assert window.streaming_url.text() == 'https://example.com/search?q={query}'
            assert window.anime_title.text() == 'Live anime'
            assert window.progress.value() == 520
            assert window.log_view.toPlainText() == 'Previous activity'
            assert window.update_status_label.text() == 'Downloading installer'
            assert window._settings_page.isAncestorOf(window._controls_card) == modern
            assert window._sidebar.isHidden() != modern
            assert window._tabs_host.isHidden() == modern
        bridge.anilist_login.emit({'generation': 7, 'token': 'verified-token', 'account': 'Alice', 'message': ''})
        app.processEvents()
        assert cfg.data.anilist_access_token == 'verified-token'
        window._nav_buttons[0].click()
        assert window.stack.currentIndex() == 0
        window._nav_buttons[0].click()
        assert window._nav_buttons[0].isChecked()
        opened = []
        monkeypatch.setattr('src.ui.app.webbrowser.open', lambda url: opened.append(url))
        window._more_links[3][0].trigger()  # Copy URL delegates to the original control.
        assert app.clipboard().text() == 'https://example.com/episode-4'
        window.open_site_btn.click()
        assert opened == ['https://example.com/episode-4']
        window.next_ep_btn.setEnabled(True)
        window.open_pwa_btn.setEnabled(False)
        window._sync_more_actions()
        assert not window._more_links[0][0].isEnabled()
    finally:
        window._quitting = True
        window.close()
        window.deleteLater()
        app.processEvents()
