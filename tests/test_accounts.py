"""Per-account profiles: switching loads each account's own watchlog save file."""
import os
import sys

import pytest
from PySide6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.config import ConfigManager
from src.core.controller import Controller
from src.ui.bridge import UiBridge


@pytest.fixture(scope="module")
def app():
    existing = QApplication.instance()
    if existing is not None:
        return existing
    return QApplication(sys.argv)


def make_controller(tmp_path):
    cfg = ConfigManager(str(tmp_path / "config.json"))
    cfg.set(rpc_enabled=False, detection_enabled=True, extension_port=0)
    return Controller(cfg, UiBridge())


def test_add_switch_isolates_watchlogs(tmp_path, app, monkeypatch):
    import src.core.controller as ctrl_mod

    monkeypatch.setattr(ctrl_mod, "APP_DIR", tmp_path)
    ctrl = make_controller(tmp_path)

    assert ctrl.active_anilist_account == ""
    assert ctrl.anilist_accounts == []

    # Legacy single-file data is migrated into the first account's save file.
    ctrl.watchlog.add("Shared Show", 1, "site", "u", "", 100.0)
    ctrl.add_anilist_account("Alice")
    assert ctrl.active_anilist_account == "Alice"
    assert (tmp_path / "watchlog-alice.json").exists()
    assert not (tmp_path / "watchlog.json").exists()
    assert ctrl.watchlog.total_seconds() == pytest.approx(100.0)

    # New recording goes to Alice's file.
    ctrl.watchlog.add("Alice Show", 2, "site", "u", "", 50.0)

    # A second account starts from an empty file of its own.
    ctrl.add_anilist_account("Bob")
    assert ctrl.active_anilist_account == "Bob"
    assert ctrl.watchlog.total_seconds() == 0.0

    # Switching back reloads Alice's save file.
    ctrl.switch_anilist_account("Alice")
    assert ctrl.watchlog.total_seconds() == pytest.approx(150.0)

    # Removing a non-active account leaves the active one untouched.
    ctrl.remove_anilist_account("Bob")
    assert ctrl.active_anilist_account == "Alice"
    assert "Bob" not in ctrl.anilist_accounts


def test_switch_to_default_profile(tmp_path, app, monkeypatch):
    import src.core.controller as ctrl_mod

    monkeypatch.setattr(ctrl_mod, "APP_DIR", tmp_path)
    ctrl = make_controller(tmp_path)

    ctrl.add_anilist_account("Alice")
    ctrl.watchlog.add("A", 1, "site", "u", "", 60.0)

    ctrl.switch_anilist_account("")
    assert ctrl.active_anilist_account == ""
    assert ctrl.watchlog.total_seconds() == 0.0

    ctrl.switch_anilist_account("Alice")
    assert ctrl.watchlog.total_seconds() == pytest.approx(60.0)


def test_add_existing_account_is_noop_dup(tmp_path, app, monkeypatch):
    import src.core.controller as ctrl_mod

    monkeypatch.setattr(ctrl_mod, "APP_DIR", tmp_path)
    ctrl = make_controller(tmp_path)

    ctrl.add_anilist_account("Alice")
    ctrl.add_anilist_account("Alice")
    assert ctrl.anilist_accounts == ["Alice"]


def test_categorize_list_entry_groups_by_status(tmp_path, app, monkeypatch):
    from src.anime.anilist import Media

    monkeypatch.setattr("src.core.controller.APP_DIR", tmp_path)
    ctrl = make_controller(tmp_path)

    media = Media(
        id=1, title_romaji="Show", site_url="https://anilist.co/anime/1",
        episodes=24,
    )
    records, sections = [], {"Watching": [], "Planning": [], "Completed": []}
    for status, progress in (("CURRENT", 5), ("PLANNING", 0), ("COMPLETED", 24)):
        records, sections = ctrl._categorize_list_entry(
            {"media": media, "status": status, "progress": progress},
            media, records, sections,
        )

    assert [e["title"] for e in sections["Watching"]] == ["Show"]
    assert sections["Planning"][0]["status"] == "PLANNING"
    assert sections["Completed"][0]["progress"] == 24
    # Only current/completed/repeating with progress seed watch time.
    assert len(records) == 2
    assert records[0]["seconds"] == 5 * 1440
