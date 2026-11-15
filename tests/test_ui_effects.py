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
    monkeypatch.setattr("src.ui.browse.catalogue", lambda *args: {"entries": [], "hasNextPage": False})
    monkeypatch.setattr("src.ui.details.load_details", lambda *args: {})
    windows = []

    def create(**settings):
        cfg = ConfigManager(tmp_path / f"config-{len(windows)}.json")
        cfg.set(auto_check_updates=False, chrome_auto_launch=False, onboarding_complete=True, ui_layout="modern")
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


def test_cached_avatar_arrives_during_startup(make_window, monkeypatch, tmp_path):
    from PySide6.QtGui import QColor, QImage
    from src.core.artwork import ArtworkService
    url = "https://avatar.example/alice.png"
    cache = ArtworkService(tmp_path, lambda: None).image_path(url)
    cache.parent.mkdir(parents=True, exist_ok=True)
    image = QImage(80, 80, QImage.Format_ARGB32)
    image.fill(QColor("#ca37ee")); image.save(str(cache), "PNG")
    monkeypatch.setattr(Controller, "fetch_account_avatar",
                        lambda self, name: self.bridge.account_avatar.emit(name, url))
    app, window = make_window(anilist_accounts=["Alice"], anilist_username="Alice")
    assert window._account_avatar_url == url
    assert not window._sidebar_account.icon().isNull()
    assert window.account_avatar.pixmap().toImage().pixelColor(17, 17).name() == "#ca37ee"


def test_browse_stale_responses_badge_and_confirmed_addition(make_window, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    app, window = make_window(anilist_accounts=["Alice"], anilist_username="Alice", smooth_animations=False)
    panel = window._browse
    assert window._page_names[:3] == ["Overview", "Browse", "History"]
    panel.started = True
    entry = {"anilist_id": 42, "title": "Example", "episodes": 12}
    panel.receive(panel.generation, 1, {"entries": [entry], "hasNextPage": True, "total": 2})
    assert panel.grid.count() == 1
    old = panel.generation
    panel.search.setText("Other"); panel.timer.stop()
    panel.receive(old, 2, {"entries": [{"anilist_id": 99, "title": "Stale"}]})
    assert panel.grid.count() == 1
    window.show(); window.tabbar.setCurrentIndex(window._browse_tab_index); app.processEvents()
    opened = []
    monkeypatch.setattr("src.ui.app.webbrowser.open", opened.append)
    QTest.mouseClick(panel.grid.viewport(), Qt.LeftButton,
                     pos=panel.grid.badge_rect(panel.grid.item(0)).center().toPoint())
    assert not opened
    menu = window._library_modern._status_menu
    assert not any(action.isChecked() for action in menu.actions())
    menu.close()
    window._on_library_changed({"account": "Alice", "media_id": 42, "entry": entry, "status": "PLANNING",
                                "result": {"status": "PLANNING", "progress": 0}})
    assert panel.grid.item(0).data(Qt.UserRole)["status"] == "PLANNING"
    assert window._library_modern.entries[0]["anilist_id"] == 42


def test_detail_navigation_stale_results_pagination_and_watch(make_window, monkeypatch):
    app, window = make_window(smooth_animations=False)
    opened = []
    monkeypatch.setattr("src.ui.app.webbrowser.open", opened.append)
    window.show(); app.processEvents()
    window._open_anime({"title": "First", "anilist_id": 1})
    page = window._details
    old = page.generation
    window._open_anime({"title": "Second", "anilist_id": 2, "episodes": 1200})
    assert not opened
    assert len(page.cards) == 12
    page.receive(old, {"title": "Wrong title"})
    assert page.entry["title"] == "Second"
    page.ranges.setCurrentIndex(99)
    assert page.cards[0][0] == 1189 and page.cards[-1][0] == 1200
    page.watch_episode(1200)
    assert opened == ["https://ani.pm/anime?q=Second"]
    page.go_back()
    assert page.entry["title"] == "First"
    page.go_back()
    assert window.stack.currentIndex() == window.tabbar.currentIndex()
    window._open_anime({"title": "Again"})
    window._navigate(window.tabbar.currentIndex())
    assert window.stack.currentWidget() is not page


def test_v3_live_switching_keeps_details_playback_and_all_versions(make_window):
    app,window=make_window(ui_layout="cinema",smooth_animations=False)
    window.show();app.processEvents()
    assert window.layout_combo.count()==3
    assert not hasattr(window,"modern_ui_check")
    assert window._sidebar.isHidden()
    assert window.stack.currentWidget() is window._cinema_overview
    window._on_detection({"title":"Playing","episode":7,"url":"https://ani.pm/test"})
    window._open_anime({"title":"Example","anilist_id":42,"episodes":25})
    page=window._details
    page.search_episode.setText("25");page.jump_episode()
    assert page.cards[0][0]==25 and len(page.cards)==1
    page.search_episode.setText("999");page.jump_episode()
    assert len(page.cards)==1
    for index,mode in ((0,"classic"),(1,"modern"),(2,"cinema")):
        window.layout_combo.setCurrentIndex(index);app.processEvents()
        assert window.cfg.data.ui_layout==mode
        assert window.stack.currentWidget() is window._details
        assert window._details.entry["anilist_id"]==42
        assert window._current_payload["title"]=="Playing"
    window._details.go_back()
    assert window.stack.currentWidget() is window._cinema_overview
    window._navigate(window._stats_tab_index)
    assert window.stack.currentWidget() is window._cinema_stats
    window._cinema_stats.select(365)
    assert len(window._cinema_stats.chart.series)==53
    window.resize(760,800);app.processEvents()
    assert window._cinema_stats.widget().width()<=window._cinema_stats.viewport().width()
    window._navigate(window._history_tab_index)
    assert window.stack.currentWidget() is window._cinema_history
    window.show_placeholder()
    assert not window._cinema_overview.payload


def test_v3_history_filters_groups_and_removal(make_window):
    app,window=make_window(ui_layout="cinema",smooth_animations=False)
    window.controller.recent.add({"title":"First","episode":1,"url":"https://ani.pm/1"})
    window.controller.recent.add({"title":"Second","episode":2,"url":"https://ani.pm/2"})
    panel=window._cinema_history
    panel.refresh()
    assert len(panel.cards)==2
    panel.search.setText("First")
    assert len(panel.cards)==1
    panel.remove(panel.cards[0].entry)
    assert not panel.cards
    panel.search.clear()
    assert len(panel.cards)==1 and panel.cards[0].entry["title"]=="Second"


def test_overview_logo_fallback_and_account_flyout(make_window, monkeypatch, tmp_path):
    from PySide6.QtGui import QPixmap, QColor
    app, window = make_window(overview_title_logo=True, anilist_accounts=["Alice", "Bob"],
                              anilist_username="Alice", anilist_access_token="alice-test",
                              anilist_sync_account="Alice", anilist_sessions={"bob": "bob-test"})
    window._current_payload = {"title": "Example", "title_logo": "https://test/logo.png"}
    pm = QPixmap(600, 150); pm.fill(QColor("white"))
    monkeypatch.setattr(window._images, "get", lambda *args: pm)
    window._refresh_overview_logo()
    assert not window.anime_logo.isHidden() and window.anime_title.isHidden()
    window._current_payload = {"title": "No logo"}
    window._refresh_overview_logo()
    assert window.anime_logo.isHidden() and not window.anime_title.isHidden()
    window._show_account_menu()
    menu = window._account_menu
    next(action for action in menu.actions() if action.text().startswith("Bob")).trigger()
    assert window.cfg.data.anilist_access_token == "bob-test"
    assert window.controller.active_anilist_account == "Bob"
    menu.close()
    window._on_anilist_disconnect()
    assert "bob" not in window.cfg.data.anilist_sessions
    window._switch_account_menu("Alice")
    assert window.cfg.data.anilist_access_token == "alice-test"


def test_oled_custom_font_theme_art_and_settings_cleanup(make_window, monkeypatch, tmp_path):
    import base64
    from src.fonts import _FIGTREE_B64
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QPixmap, QFont, QFontDatabase
    from src.ui.v3_widgets import illustrated_surface
    from src.ui.app import ACCENT_PRESETS
    app, window = make_window(theme="oled", smooth_animations=False)
    window.show(); app.processEvents()
    assert window._applied_theme == "oled"
    assert window.centralWidget().grab().toImage().pixelColor(0, 0).name() == "#000000"
    assert window._ambient.isHidden()
    assert not hasattr(window, "watch_any_btn")
    assert not hasattr(window, "launch_chrome_btn")
    assert not hasattr(window, "pwa_url_edit")
    images = [illustrated_surface(QSize(500, 112), 1, name, accent, "dark", QPixmap()).toImage()
              for name, accent in ACCENT_PRESETS]
    assert all(images[i] != images[j] for i in range(len(images)) for j in range(i))
    source = tmp_path / "custom.ttf"; source.write_bytes(base64.b64decode(_FIGTREE_B64))
    monkeypatch.setattr("PySide6.QtWidgets.QFileDialog.getOpenFileName", lambda *a: (str(source), ""))
    window._import_font()
    saved = ConfigManager(window.cfg.path).data
    assert saved.font_family == "Figtree" and saved.font_path
    family = QFontDatabase.families()[0]
    window.font_combo.setCurrentFont(QFont(family))
    assert window.cfg.data.font_family == family
    assert f'font-family: "{family}"' in app.styleSheet()
    window._set_font_family("Missing font family")
    assert 'font-family: "Figtree"' in app.styleSheet()
    window._show_update_settings()
    assert window._updates_section.toggle.isChecked()


def test_library_filters_sorting_account_reset_and_layout_switch(make_window):
    from PySide6.QtCore import Qt
    app, window = make_window(anilist_accounts=["Alice", "Bob"], anilist_username="Alice", smooth_animations=False)
    rows = [{"title": "Zeta", "anilist_id": 1, "episodes": 24, "mean_score": 90, "created_at": 10,
             "progress": 4, "genres": ["Action"], "season_year": 2024},
            {"title": "Romaji", "title_english": "Alpha", "aliases": ["Romaji"], "anilist_id": 2,
             "episodes": 12, "mean_score": 80, "created_at": 20, "genres": ["Drama"], "season_year": 2025}]
    window._on_library_loaded({"username": "Alice", "sections": {"Watching": rows, "Planning": [rows[0]]}})
    panel = window._library_modern
    assert len(panel.entries) == 2
    assert panel.buttons["All"].text() == "All  2"
    assert panel.grid.item(0).data(Qt.UserRole)["title"] == "Alpha"
    panel.sort.setCurrentIndex(2)
    assert panel.grid.item(0).data(Qt.UserRole)["title"] == "Zeta"
    panel.search.setText("romaji")
    assert panel.grid.count() == 1
    panel.search.clear(); panel.genre.setCurrentText("Action")
    assert panel.grid.count() == 1
    panel.reset_filters()
    window.show(); window.resize(600, 900)
    window.tabbar.setCurrentIndex(window._library_tab_index)
    for _ in range(5): app.processEvents()
    assert panel.grid.visualItemRect(panel.grid.item(1)).x() > 0
    assert panel.grid.visualItemRect(panel.grid.item(1)).right() < panel.grid.viewport().width()
    window.layout_combo.setCurrentIndex(0)
    assert panel.isHidden()
    assert window._library_section_lists["Watching"].count() == 2
    window.layout_combo.setCurrentIndex(1)
    assert not panel.isHidden()
    window.anilist_combo.setCurrentIndex(window.anilist_combo.findData("Bob"))
    assert not panel.entries
    window._on_library_loaded({"username": "Alice", "sections": {"Watching": rows}})
    assert not panel.entries
    window.cover_source_combo.setCurrentIndex(1)
    assert window._artwork.cover_source == "anilist"
    assert ConfigManager(window.cfg.path).data.cover_source == "anilist"


def test_settings_wheel_scrolls_page_without_changing_controls(make_window):
    from PySide6.QtCore import Qt, QPoint, QPointF
    from PySide6.QtGui import QWheelEvent
    app,window=make_window(smooth_animations=False)
    window.show();window.resize(800,700);window.tabbar.setCurrentIndex(window._settings_tab_index)
    app.processEvents()
    bar=window._settings_page.verticalScrollBar()
    assert bar.maximum()>300 and not bar.isVisible()
    for control in (window.browser_combo,window.interval_spin,window.interval_spin.lineEdit()):
        before=(window.browser_combo.currentIndex(),window.interval_spin.value())
        bar.setValue(100)
        control.setFocus()
        event=QWheelEvent(QPointF(5,5),QPointF(control.mapToGlobal(QPoint(5,5))),QPoint(),QPoint(0,-120),
                          Qt.NoButton,Qt.NoModifier,Qt.NoScrollPhase,False)
        app.sendEvent(control,event)
        assert (window.browser_combo.currentIndex(),window.interval_spin.value())==before
        assert bar.value()>100


def test_library_badge_menu_and_confirmed_status_updates(make_window):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    app,window=make_window(anilist_accounts=["Alice"],anilist_username="Alice",smooth_animations=False)
    entry={"title":"Example","anilist_id":42,"status":"PLANNING","progress":7,"episodes":12}
    window._on_library_loaded({"username":"Alice","sections":{"Planning":[entry]}})
    panel=window._library_modern;requested=[]
    panel.status_changed.disconnect()
    panel.status_changed.connect(lambda entry,status:requested.append(status))
    window.show();window.tabbar.setCurrentIndex(window._library_tab_index);app.processEvents()
    QTest.mouseClick(panel.grid.viewport(),Qt.LeftButton,pos=panel.grid.badge_rect(panel.grid.item(0)).center().toPoint())
    menu=panel._status_menu
    actions={a.data():a for a in menu.actions() if a.data()}
    assert set(actions)=={"CURRENT","REPEATING","PLANNING","COMPLETED","PAUSED","DROPPED","REMOVE"}
    assert actions["PLANNING"].isChecked()
    actions["REPEATING"].trigger();menu.close()
    assert requested==["REPEATING"]
    window._on_library_changed({"account":"Alice","media_id":42,"status":"REPEATING","error":"Offline"})
    assert panel.entries[0]["status"]=="PLANNING"
    window._on_library_changed({"account":"Alice","media_id":42,"status":"REPEATING",
                               "result":{"status":"REPEATING","progress":7}})
    assert panel.entries[0]["status"]=="REPEATING"
    assert panel.entries[0]["progress"]==7
    assert panel.buttons["Watching"].text()=="Watching  1"
    window._on_library_changed({"account":"Bob","media_id":42,"status":"REMOVE","result":{"removed":True}})
    assert len(panel.entries)==1
    window._on_library_changed({"account":"Alice","media_id":42,"status":"REMOVE","result":{"removed":True}})
    assert not panel.entries


def test_artwork_notifications_and_cached_navigation_do_not_rebuild_lists(make_window,monkeypatch):
    app,window=make_window(smooth_animations=False)
    window._on_recent([{"title":"Example","episode":1}])
    window._on_library_loaded({"username":"","sections":{"Watching":[{"title":"Example","anilist_id":1}]}})
    window.show();app.processEvents()
    monkeypatch.setattr(window._history_modern,"refresh",lambda:pytest.fail("history rebuilt"))
    monkeypatch.setattr(window,"_refresh_stats",lambda:pytest.fail("stats rebuilt for artwork"))
    monkeypatch.setattr(window,"_on_refresh_library",lambda:pytest.fail("cached library refetched"))
    window._refresh_artwork_views()
    for index in (1,window._library_tab_index,window._settings_tab_index,1,window._library_tab_index):
        window.tabbar.setCurrentIndex(index)
        app.processEvents()


def test_history_loads_old_rows_in_batches_and_searches_all_entries(make_window):
    app,window=make_window();panel=window._history_modern
    panel.set_entries([{"title":"Example","episode":n,"last_watched":1000000} for n in range(1,1301)])
    assert panel.count.text()=="1300 entries"
    assert panel.list.count()<300
    while panel._shown<len(panel.filtered):panel._append_history()
    assert panel._shown==1300
    panel.set_entries([{"title":f"Anime {n}","episode":1} for n in range(1300)])
    panel.search.setText("Anime 1299")
    assert panel.count.text()=="1 entries"


def test_image_cache_decodes_once_and_retains_high_resolution(make_window,tmp_path):
    from PySide6.QtGui import QImage,QColor
    app,window=make_window()
    image=QImage(1920,1080,QImage.Format_RGB32);image.fill(QColor("#6d389f"))
    path=tmp_path/"episode.png";image.save(str(path))
    cache=window._images
    assert cache.get(path,1536,864).isNull()
    assert cache.get(path,1536,864).isNull()
    assert len(cache.pending)==1
    loop=QEventLoop();cache.ready.connect(loop.quit);QTimer.singleShot(3000,loop.quit);loop.exec()
    pixmap=cache.get(path,1536,864)
    assert pixmap.width()==1536 and pixmap.height()==864
    assert not cache.pending and cache.bytes<=cache.budget
    assert cache.get(path,1536,864).cacheKey()==pixmap.cacheKey()


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


def test_continue_cards_use_local_progress_and_keep_activation_current(make_window, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    app, window = make_window(smooth_animations=False, streaming_site="Custom", streaming_search_url="https://watch/search?q={query}")
    ctrl = window.controller
    ctrl.watchlog.add("Example", 2, "Browser", "https://watch/2", "", 300, media_id=42)
    ctrl.recent.add({"title": "Example", "anilist_id": 42, "episode": 2,
                     "episode_title": "The beginning", "url": "https://watch/2", "current_time": 300, "duration": 1200})
    window._refresh_continue()
    panel = window._continue_frame
    assert len(panel.cards) == 1
    card = panel.cards[0]
    assert card.entry["current_time"] == 300
    assert card.entry["episode_title"] == "The beginning"
    window.show(); app.processEvents()
    assert not card.grab().isNull()
    opened = []
    monkeypatch.setattr("src.ui.app.webbrowser.open", opened.append)
    card.setFocus(); QTest.keyClick(card, Qt.Key_Space)
    assert not opened
    assert window.stack.currentWidget() is window._details
    window._details.watch.click()
    assert opened == ["https://watch/2"]
    window._details.go_back()
    ctrl.recent.add({"title": "Example", "anilist_id": 42, "episode": 3, "url": "https://watch/3"})
    window._refresh_continue()
    assert panel.cards[0] is card
    QTest.keyClick(card, Qt.Key_Space)
    window._details.watch.click()
    assert opened[-1] == "https://watch/3"
    # Progress from episode 2 must not carry over to episode 3.
    assert not card.entry.get("duration")
    panel.view_all.emit()
    assert window.stack.currentIndex() == window._history_tab_index


def test_history_renders_episode_names_and_numeric_runs(make_window):
    from PySide6.QtCore import Qt
    from src.ui.anime_list import DETAIL_ROLE
    app, window = make_window()
    window._on_recent([{"title": "Example", "episode": 10, "last_watched": 30, "episode_title": "Ten"},
                       {"title": "Example", "episode": 2, "last_watched": 20, "episode_title": "Two"}])
    panel = window._history_modern
    entries = [panel.list.item(i) for i in range(panel.list.count()) if panel.list.item(i).data(Qt.UserRole+1)]
    assert [e.data(Qt.UserRole+1)["episode"] for e in entries] == [10, 2]
    assert "Ten" in entries[0].data(DETAIL_ROLE)
    assert "Ten" in window.recent_list.item(0).data(DETAIL_ROLE)
    panel.search.setText("Two")
    assert "1 entries" in panel.count.text()


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
    window.tabbar.setCurrentIndex(window._history_tab_index)
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
    window.tabbar.setCurrentIndex(window._history_tab_index)
    assert window._tab_overlay is None
    window.animations_check.setChecked(True)
    window.tabbar.setCurrentIndex(0)
    assert window._tab_overlay is not None
    window.layout_combo.setCurrentIndex(0)
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
    window.layout_combo.setCurrentIndex(0)
    window.glass_check.setChecked(True)
    assert window.centralWidget().glass
    assert "border-top-color" in app.styleSheet()
    saved = ConfigManager(window.cfg.path).data
    assert saved.glass_effects and saved.ui_layout == "classic"


def test_anime_selection_has_no_focus_box_and_keeps_keyboard_activation(make_window, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QStyle, QStyleOptionViewItem
    app, window = make_window(smooth_animations=False, streaming_site="Custom", streaming_search_url="https://example.com/search?q={query}")
    window._on_recent([{"title": "First", "url": "https://example.com/1"},
                       {"title": "Second", "url": "https://example.com/2"}])
    window.show()
    window.tabbar.setCurrentIndex(window._history_tab_index)
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
    assert not opened
    assert window._details.entry["title"] == "Second"
    window._details.watch.click()
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
