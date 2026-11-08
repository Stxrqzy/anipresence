"""Settings wiring and async callbacks survive a theme/UI rebuild."""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication

from src.config import ConfigManager
from src.core.controller import Controller
from src.ui.app import MainWindow
from src.ui.bridge import UiBridge


def test_settings_controls_and_rebuild(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr('src.core.controller.APP_DIR', tmp_path)
    monkeypatch.setattr('src.ui.app.APP_DIR', tmp_path)
    monkeypatch.setattr(MainWindow, '_build_tray', lambda self: None)
    for method in ('refresh_library', 'refresh_calendar', 'fetch_account_avatar'):
        monkeypatch.setattr(Controller, method, lambda *a, **kw: None)
    cfg = ConfigManager(tmp_path/'config.json')
    cfg.set(auto_check_updates=False, chrome_auto_launch=False)
    bridge = UiBridge()
    ctrl = Controller(cfg, bridge)
    window = MainWindow(cfg, bridge, ctrl)
    try:
        window.presence_options['presence_cover_art'].setChecked(False)
        assert not cfg.data.presence_cover_art
        window._on_anilist_login()  # setup guidance rather than opening an unregistered client
        assert 'client ID' in window.anilist_login_status.text()
        window._on_update_status({'stage': 'available', 'release': {'version': '9.0.0', 'url': 'https://example.com/setup.exe'},
                                  'message': 'New release'})
        assert window.update_download_btn.isEnabled()
        window._rebuild_ui()
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
