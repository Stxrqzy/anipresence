"""Episode-progress and airing-status formatting helpers."""
import time

from src.anime.titles import (
    format_episode_progress, format_season, next_airing_label,
)


def test_format_episode_progress_plain():
    assert format_episode_progress(7, None) == "Episode 7"
    assert format_episode_progress(0, 12) == ""
    assert format_episode_progress(None, 12) == ""


def test_format_episode_progress_with_total():
    assert format_episode_progress(7, 12) == "Episode 7 of 12"


def test_format_season():
    assert format_season("SUMMER", 2024) == "Summer 2024"
    assert format_season("", 2024) == ""
    assert format_season("WINTER", None) == "Winter"


def test_next_airing_label_empty_when_no_schedule():
    assert next_airing_label(None, None, 3) == ""
    assert next_airing_label(0, None, 3) == ""


def test_next_airing_label_skips_when_user_is_past():
    # Watching an old episode shouldn't advertise the upcoming release.
    assert next_airing_label(1723000000, 13, 20) == ""


def test_next_airing_label_weekday():
    at = 1723000000
    weekday = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[
        time.localtime(at).tm_wday
    ]
    assert next_airing_label(at, 13, 5) == f"Next: Ep 13 · {weekday}"
    assert next_airing_label(at, None, 5) == f"Next: {weekday}"
