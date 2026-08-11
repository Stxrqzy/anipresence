"""WatchLog persistence and analytics helpers."""
from datetime import date, timedelta
from pathlib import Path

import pytest

from src.core.watchlog import WatchLog, account_slug, format_hours, watchlog_path_for


@pytest.fixture()
def log(tmp_path):
    return WatchLog(path=tmp_path / "watchlog.json")


def test_add_rolls_into_days_and_titles(log):
    log.add("One Piece", 1104, "Miruro", "https://miruro.tv/ep/1104", "https://c/op.jpg", 1800.0)
    log.add("One Piece", 1104, "Miruro", "https://miruro.tv/ep/1104", "https://c/op.jpg", 1800.0)
    log.add("Vinland Saga", 3, "Crunchyroll", "https://cr/3", "https://c/vs.jpg", 600.0)

    today = date.today().isoformat()
    days = log.days()
    assert days[today] == pytest.approx(4200.0)
    titles = log.titles()
    assert titles["One Piece"]["seconds"] == pytest.approx(3600.0)
    assert titles["One Piece"]["cover"] == "https://c/op.jpg"
    assert titles["Vinland Saga"]["seconds"] == pytest.approx(600.0)


def test_add_ignores_empty_and_nonpositive(log):
    log.add("", 1, "site", "u", "", 10.0)
    log.add("Title", 1, "site", "u", "", 0)
    log.add("Title", 1, "site", "u", "", -5)
    assert log.days() == {}
    assert log.titles() == {}


def test_persists_to_disk(tmp_path):
    path = tmp_path / "watchlog.json"
    first = WatchLog(path=path)
    first.add("A", 1, "s", "u", "https://c/a.jpg", 100.0)
    second = WatchLog(path=path)
    assert second.days()[date.today().isoformat()] == pytest.approx(100.0)


def test_corrupt_file_resets(log, tmp_path):
    (tmp_path / "watchlog.json").write_text("{not json", encoding="utf-8")
    fresh = WatchLog(path=tmp_path / "watchlog.json")
    assert fresh.days() == {}
    assert fresh.titles() == {}


def _set_days(log, days):
    log._days = {d.isoformat(): s for d, s in days}
    return log


def test_current_streak_ends_today(log):
    today = date.today()
    log = _set_days(log, [(today - timedelta(days=i), 60.0) for i in range(4)])
    assert log.current_streak() == 4


def test_streak_breaks_on_gap(log):
    today = date.today()
    log = _set_days(
        log,
        [
            (today, 60.0),
            (today - timedelta(days=1), 0.0),
            (today - timedelta(days=2), 60.0),
        ],
    )
    assert log.current_streak() == 1


def test_streak_tolerates_empty_today(log):
    today = date.today()
    log = _set_days(log, [(today - timedelta(days=i), 60.0) for i in range(1, 4)])
    assert log.current_streak() == 3


def test_last_n_days_spans_zero_days(log):
    today = date.today()
    log = _set_days(log, [(today, 7200.0)])
    pairs = log.last_n_days(7)
    assert len(pairs) == 7
    assert pairs[-1] == (today.isoformat(), 7200.0)
    assert all(seconds == 0.0 for day, seconds in pairs[:-1])


def test_top_titles_ordered_with_resume(log):
    log.add("B", 2, "site", "https://b/2", "https://c/b.jpg", 100.0)
    log.add("A", 1, "site", "https://a/1", "https://c/a.jpg", 400.0)
    top = log.top_titles(limit=5)
    assert [t["title"] for t in top] == ["A", "B"]
    assert top[0]["url"] == "https://a/1"
    assert top[0]["episode"] == 1


def test_clear_resets_all_data(log):
    log.add("A", 1, "site", "u", "", 100.0)
    log.clear()
    assert log.days() == {}
    assert log.titles() == {}
    assert log.total_seconds() == 0.0


def test_continue_list_newest_first_with_urls(log):
    log.add("Old", 3, "site", "https://old/3", "https://c/o.jpg", 100.0)
    log.add("New", 8, "site", "https://new/8", "https://c/n.jpg", 100.0)
    log.add("NoUrl", 2, "site", "", "", 100.0)
    cont = log.continue_list(limit=10)
    assert [t["title"] for t in cont] == ["New", "Old"]
    assert cont[0]["url"] == "https://new/8"
    assert cont[0]["episode"] == 8


def test_continue_list_respects_limit(log):
    log.add("A", 1, "site", "https://a/1", "", 100.0)
    log.add("B", 1, "site", "https://b/1", "", 100.0)
    log.add("C", 1, "site", "https://c/1", "", 100.0)
    assert len(log.continue_list(limit=2)) == 2


def test_continue_list_empty(log):
    log.add("NoUrl", 2, "site", "", "", 100.0)
    assert log.continue_list() == []


def test_import_titles_seeds_totals_not_daily(log):
    log.add("A", 1, "site", "u", "", 100.0)
    imported = log.import_titles(
        [
            {"title": "A", "seconds": 7200, "episode": 24, "url": "https://a/24"},
            {"title": "B", "seconds": 1440, "website": "AniList"},
            {"title": "", "seconds": 999},
            {"title": "C", "seconds": 0},
        ]
    )
    assert imported == pytest.approx(8640.0)
    assert log.total_seconds() == pytest.approx(8740.0)
    assert log.days() == {date.today().isoformat(): pytest.approx(100.0)}
    assert log.titles()["A"]["seconds"] == pytest.approx(7300.0)
    assert log.titles()["A"]["episode"] == 24
    assert log.titles()["B"]["website"] == "AniList"
    assert "C" not in log.titles()


def test_import_titles_empty_returns_zero(log):
    assert log.import_titles([]) == 0
    assert log.import_titles([{"title": "X", "seconds": 0}]) == 0


def test_anilist_sections_roundtrip(log, tmp_path):
    log.set_anilist_sections(
        {
            "Watching": [{"title": "A", "progress": 5}],
            "Planning": [{"title": "B"}],
        }
    )
    assert log.anilist_sections()["Watching"] == [{"title": "A", "progress": 5}]

    reloaded = WatchLog(path=log._path)
    assert reloaded.anilist_sections()["Planning"] == [{"title": "B"}]
    assert "Completed" not in reloaded.anilist_sections()


def test_clear_keeps_anilist_sections(log):
    log.set_anilist_sections({"Watching": [{"title": "A"}]})
    log.add("A", 1, "site", "u", "", 100.0)
    log.clear()
    assert log.total_seconds() == 0.0
    assert log.anilist_sections()["Watching"] == [{"title": "A"}]
    assert WatchLog(path=log._path).anilist_sections()["Watching"] == [{"title": "A"}]


@pytest.mark.parametrize(
    "name,expected",
    [
        ("NarutoFan", "narutofan"),
        ("user_name", "user_name"),
        ("  Spaces & 2% ", "spaces___2"),
        ("", "account"),
        (None, "account"),
    ],
)
def test_account_slug(name, expected):
    assert account_slug(name) == expected


def test_watchlog_path_for_separates_accounts(tmp_path):
    default = watchlog_path_for("", tmp_path)
    alice = watchlog_path_for("Alice", tmp_path)
    bob = watchlog_path_for("bob", tmp_path)
    assert default == tmp_path / "watchlog.json"
    assert alice == tmp_path / "watchlog-alice.json"
    assert bob == tmp_path / "watchlog-bob.json"
    assert alice != bob != default


def test_remove_title_subtracts_from_daily_totals(log):
    today = date.today().isoformat()
    log.add("A", 1, "site", "u", "", 200.0)
    log.add("B", 1, "site", "u", "", 100.0)
    assert log.days()[today] == pytest.approx(300.0)
    log.remove_title("A")
    assert "A" not in log.titles()
    assert log.days()[today] == pytest.approx(100.0)
    assert log.total_seconds() == pytest.approx(100.0)


def test_remove_title_unknown_is_noop(log):
    log.add("A", 1, "site", "u", "", 100.0)
    before = log.days()
    log.remove_title("Missing")
    assert log.days() == before


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (0, "0m"),
        (30, "0m"),
        (90, "2m"),
        (3600, "1h"),
        (7200, "2h"),
        (7500, "2h 5m"),
    ],
)
def test_format_hours(seconds, expected):
    assert format_hours(seconds) == expected
