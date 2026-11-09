"""Startup update delivery and interruptible UI animation lifecycles."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QAbstractAnimation, QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from src.config import ConfigManager
from src.core.controller import Controller
from src.fonts import register_fonts
from src.ui.app import MainWindow
from src.ui.bridge import UiBridge


@pytest.fixture
def make_window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    register_fonts()
    monkeypatch.setattr("src.core.controller.APP_DIR", tmp_path)
    monkeypatch.setattr("src.ui.app.APP_DIR", tmp_path)
    for method in ("_build_tray", "_install_resize_filter", "_apply_native_window_styles", "_apply_backdrop"):
        monkeypatch.setattr(MainWindow, method, lambda *args: None)
    for method in ("refresh_library", "refresh_calendar", "fetch_account_avatar"):
        monkeypatch.setattr(Controller, method, lambda *args: None)
    windows = []

    def create(**settings):
        cfg = ConfigManager(tmp_path / f"config-{len(windows)}.json")
        cfg.set(auto_check_updates=False, chrome_auto_launch=False, onboarding_complete=True)
        cfg.set(**settings)
        bridge = UiBridge()
        window = MainWindow(cfg, bridge, Controller(cfg, bridge))
        windows.append(window)
        return app, window

    yield create
    for window in windows:
        window._quitting = True
        window.close()
        window.deleteLater()
    app.processEvents()


def test_startup_update_is_immediate_and_surfaces_available_release(make_window, monkeypatch):
    requests = []
    def release(feed):
        requests.append(feed)
        return {"version": "99.0.0", "url": "https://example.com/setup.exe"}
    monkeypatch.setattr("src.updates.check_release", release)
    app, window = make_window(auto_check_updates=True)
    loop = QEventLoop()
    window.bridge.update_status.connect(lambda result: loop.quit())
    deadline = QTimer()
    deadline.setSingleShot(True)
    deadline.timeout.connect(loop.quit)
    deadline.start(3000)
    loop.exec()
    deadline.stop()
    assert len(requests) == 1
    assert window._update_state["stage"] == "available"
    assert not window._update_banner.isHidden()
    assert "99.0.0" in window._update_banner_label.text()
    assert not window._startup_update_timer.isActive()
    window._show_update_settings()
    assert window.stack.currentWidget() is window._settings_page
    # Rebuilding appearance does not schedule a second startup request.
    window._rebuild_ui()
    app.processEvents()
    assert len(requests) == 1
    assert not window._update_banner.isHidden()


def test_automatic_updates_respect_opt_out_busy_and_downloaded_state(make_window, monkeypatch):
    app, window = make_window()
    requests = []
    monkeypatch.setattr(window, "_check_for_updates", lambda: requests.append(True))
    app.processEvents()
    assert not requests
    window.auto_updates_check.setChecked(True)
    assert requests == [True]
    window._update_state = {"stage": "downloaded"}
    window._auto_check_updates()
    assert requests == [True]
    # The real network entry point also refuses concurrent requests.
    monkeypatch.setattr("src.updates.check_release", lambda feed: pytest.fail("duplicate request"))
    window._update_busy = True
    MainWindow._check_for_updates(window)


def test_transitions_cancel_cleanly_and_progress_finishes_on_disable(make_window):
    app, window = make_window()
    window.show()
    app.processEvents()
    window.tabbar.setCurrentIndex(1)
    first = window._tab_overlay
    assert first is not None
    window.tabbar.setCurrentIndex(window._settings_tab_index)
    assert first.isHidden()
    assert window._tab_overlay is not first
    window._tab_anim.setCurrentTime(window._tab_anim.duration())
    assert window._tab_overlay is None
    assert window.stack.currentWidget().graphicsEffect() is None
    window.tabbar.setCurrentIndex(0)
    window._tab_anim_stop()
    app.processEvents()
    payload = {"title": "Example", "url": "https://example.com/episode-1", "duration": 1000, "current_time": 100}
    window._on_detection(payload)
    window._on_detection(dict(payload, current_time=130))
    assert window._progress_animation.state() == QAbstractAnimation.Running
    window.animations_check.setChecked(False)
    assert window.progress.value() == 130
    assert window._progress_animation.state() == QAbstractAnimation.Stopped
    window.tabbar.setCurrentIndex(1)
    assert window._tab_overlay is None
    window.animations_check.setChecked(True)
    window.tabbar.setCurrentIndex(0)
    assert window._tab_overlay is not None
    window.modern_ui_check.setChecked(False)
    assert window._tab_overlay is None
    window._on_detection(dict(payload, current_time=150))
    window.show_placeholder()
    assert window.progress.value() == 0
    assert window._progress_animation.state() == QAbstractAnimation.Stopped


def test_glass_applies_on_first_open_and_persists_across_layouts(make_window):
    app, window = make_window()
    assert "QFrame#sidebar" in app.styleSheet()
    assert "border-top-color" in app.styleSheet()
    assert window.centralWidget().glass
    window.glass_check.setChecked(False)
    assert not window.centralWidget().glass
    assert "border-top-color" not in app.styleSheet()
    window.modern_ui_check.setChecked(False)
    window.glass_check.setChecked(True)
    assert window.centralWidget().glass
    assert "border-top-color" in app.styleSheet()
    saved = ConfigManager(window.cfg.path).data
    assert saved.glass_effects and saved.ui_layout == "classic"
