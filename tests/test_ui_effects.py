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
    for method in ("refresh_library", "refresh_calendar", "fetch_account_avatar", "refresh_activity_stats"):
        monkeypatch.setattr(Controller, method, lambda *args, **kwargs: None)
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


def test_feed_history_updates_both_layouts_and_preserves_episode_selection(make_window):
    from PySide6.QtCore import Qt
    from test_history import snapshot
    app, window = make_window(smooth_animations=False)
    ctrl = window.controller
    ctrl.switch_anilist_account("Alice")
    ctrl.watchlog.set_activity(snapshot())
    ctrl.recent.add({"title": "naruto", "episode": 2, "url": "https://watch/2"})
    window._on_activity_stats({"account": "Alice", "loading": False})
    panel = window._history_modern
    assert len(panel.entries) == 3
    assert window.recent_list.count() == 3
    assert {e["title"] for e in panel.entries} == {"NARUTO"}
    for i in range(panel.list.count()):
        entry = panel.list.item(i).data(Qt.UserRole + 1)
        if entry and entry["episode"] == 3:
            panel.list.setCurrentRow(i)
    window._on_activity_stats({"account": "Alice", "loading": False})
    assert panel.list.currentItem().data(Qt.UserRole + 1)["episode"] == 3
    ctrl.switch_anilist_account("Bob")
    window._populate_anilist_accounts()
    assert len(panel.entries) == 1  # shared local playback, no Alice feed
    window._on_activity_stats({"account": "Alice", "loading": False})
    assert len(panel.entries) == 1


def test_sidebar_avatar_switch_rejects_stale_metadata_and_image(make_window, monkeypatch, tmp_path):
    from PySide6.QtCore import QSize, QUrl
    from PySide6.QtGui import QColor, QPixmap
    from PySide6.QtNetwork import QNetworkReply, QNetworkRequest
    app, window = make_window(anilist_accounts=["Alice", "Bob"], anilist_username="Alice")
    monkeypatch.setattr(window._nam, "get", lambda request: None)
    alice_url, bob_url = "https://avatar/alice", "https://avatar/bob"
    window._on_account_avatar("Alice", alice_url)
    assert alice_url in window._avatar_pending_reply
    window.anilist_combo.setCurrentIndex(window.anilist_combo.findData("Bob"))
    assert "Bob" in window._sidebar_account.text()
    assert window._account_avatar_url == ""
    window._on_account_avatar("Alice", alice_url)
    assert window._account_avatar_url == ""
    pixmap = QPixmap(50, 50)
    pixmap.fill(QColor("#12cd45"))
    window._cover_cache_dir.mkdir(parents=True, exist_ok=True)
    pixmap.save(str(window._cover_cache_path(bob_url)), "PNG")
    window._on_account_avatar("Bob", bob_url)
    bob = window._sidebar_account.icon().pixmap(QSize(34, 34)).toImage()
    assert bob.pixelColor(17, 17) == QColor("#12cd45")
    assert bob.pixelColor(0, 0).alpha() == 0
    pixmap.fill(QColor("red"))
    path = tmp_path / "alice.png"
    pixmap.save(str(path))
    class Reply:
        def request(self): return QNetworkRequest(QUrl(alice_url))
        def error(self): return QNetworkReply.NoError
        def readAll(self): return path.read_bytes()
        def deleteLater(self): pass
    window._on_cover_reply(Reply())
    assert window._sidebar_account.icon().pixmap(QSize(34, 34)).toImage() == bob
    assert window.account_avatar.pixmap().toImage() == bob
    window.anilist_combo.setCurrentIndex(0)
    assert "Default profile" in window._sidebar_account.text()
    assert window._sidebar_account.icon().pixmap(QSize(34, 34)).toImage() != bob


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


def test_anime_selection_has_no_focus_box_and_keeps_keyboard_activation(make_window, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QStyle, QStyleOptionViewItem
    app, window = make_window(smooth_animations=False)
    window._on_recent([{"title": "First", "url": "https://example.com/1"},
                       {"title": "Second", "url": "https://example.com/2"}])
    window.show()
    window.tabbar.setCurrentIndex(1)
    app.processEvents()
    view = window.recent_list
    view.setCurrentRow(0)
    view.setFocus()
    option = QStyleOptionViewItem()
    option.state |= QStyle.State_HasFocus | QStyle.State_Selected
    view.itemDelegate().initStyleOption(option, view.model().index(0, 0))
    assert not option.state & QStyle.State_HasFocus
    assert option.state & QStyle.State_Selected
    opened = []
    monkeypatch.setattr("src.ui.app.webbrowser.open", opened.append)
    QTest.keyClick(view, Qt.Key_Down)
    QTest.keyClick(view, Qt.Key_Return)
    assert opened == ["https://example.com/2"]
    assert not view.grab().isNull()  # Exercise the custom delegate's paint path.


def test_stats_search_covers_all_titles_and_refresh_keeps_selection(make_window, monkeypatch):
    app, window = make_window()
    entries = [{"title": f"Anime {i:02}", "seconds": (25-i) * 3600,
                "url": f"https://example.com/{i}"} for i in range(25)]
    log = window.controller.watchlog
    log.import_titles(entries)
    log.set_anilist_sections({"Completed": entries})
    window._refresh_stats()
    assert window._stats_list.count() == 25
    window._stats_list.setCurrentRow(20)
    window.stats_search.setText("anime 20")
    assert sum(not window._stats_list.item(i).isHidden() for i in range(25)) == 1
    assert sum(not window._section_lists["Completed"].item(i).isHidden() for i in range(25)) == 1
    window._refresh_stats()
    assert window._stats_list.currentRow() == 20
    assert not window._stats_list.item(20).isHidden()
    assert window._stats_list.item(0).isHidden()


def test_stats_responsive_margins_and_readable_chart(make_window):
    app, window = make_window(smooth_animations=False)
    window.show()
    window.tabbar.setCurrentIndex(window._stats_tab_index)
    window.resize(600, 700)
    app.processEvents()
    assert window._sidebar.x() > 0
    assert window._stats_grid_columns == 2
    assert window._stats_summary_grid.getItemPosition(2)[:2] == (1, 0)
    assert window._stats_page.widget().width() <= window._stats_page.viewport().width()
    if window._chart_view is not None:
        from PySide6.QtCore import Qt
        window._update_stats_chart([("2026-11-09", 1800), ("2026-11-10", 0)])
        chart = window._chart_view.chart()
        assert chart.axes(Qt.Horizontal)[0].categories() == ["09", "10"]
        assert chart.axes(Qt.Vertical)[0].max() >= 1
        assert "Nov 09" in window._chart_caption.text()


def test_v3_history_filters_and_completion_use_recorded_timing(make_window):
    import time
    from PySide6.QtCore import Qt
    _,window=make_window()
    panel=window._history_modern
    panel.set_entries([
        {"title":"Finished", "episode":1, "duration":100, "current_time":95, "last_watched":time.time()},
        {"title":"Legacy", "episode":2, "last_watched":time.time()},
        {"title":"Old", "episode":3, "last_watched":time.time()-86400*100},
    ])
    panel.status.setCurrentText("Completed")
    entries=[panel.list.item(i).data(Qt.UserRole+1) for i in range(panel.list.count())]
    assert [e["title"] for e in entries if e]==["Finished"]
    panel.status.setCurrentIndex(0); panel.period.setCurrentText("Last 7 days")
    panel.search.setText("Legacy")
    assert panel.count.text()=="1 entries"


def test_v3_activity_persists_filters_exports_and_clears(make_window,tmp_path):
    from src.core.activity import ActivityJournal
    _,window=make_window()
    window._on_log("Connected to Discord")
    window._on_detection({"title":"Example", "episode":4, "paused":True, "url":"https://example.com"})
    panel=window._activity_panel
    panel.kind.setCurrentText("Playback")
    assert len(panel.filtered)==1
    assert panel.filtered[0]["message"]=="Paused"
    path=tmp_path/"activity.csv"
    panel.export_to(path)
    assert "Example" in path.read_text(encoding="utf-8-sig")
    reloaded=ActivityJournal(window._activity.path)
    assert len(reloaded.entries)==2
    window._on_clear_log()
    assert not ActivityJournal(window._activity.path).entries
    assert not panel.filtered


def test_v3_analytics_range_does_not_distribute_imports_into_local_days(make_window,monkeypatch):
    from datetime import date
    _,window=make_window()
    log=window.controller.watchlog
    log.add("Example", 1, "site", "", "", 3600)
    log.import_titles([{"title":"Example", "seconds":360000}])
    panel=window._analytics
    panel.refresh()
    assert panel.metrics["time"].text()=="100h"
    panel.period.setCurrentText("Last 7 days")
    assert panel.metrics["time"].text()=="1h"
    assert sum(value for _,value in panel.line.values) == 1
