"""Episode URL navigation and watch-time formatting."""
import pytest

from src.urlnav import format_watch_time, increment_episode_url


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://site.com/watch/episode-5", "https://site.com/watch/episode-6"),
        ("https://site.com/ep/5", "https://site.com/ep/6"),
        ("https://site.com/episode5", "https://site.com/episode6"),
        ("https://site.com/watch?episode=12", "https://site.com/watch?episode=13"),
        ("https://site.com/watch/7/", "https://site.com/watch/8/"),
        (None, None),
        ("", None),
        ("https://site.com/watch", None),
    ],
)
def test_increment_episode_url(url, expected):
    assert increment_episode_url(url) == expected


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (0, "0m"),
        (45, "1m"),
        (59, "1m"),
        (60, "1m"),
        (3599, "1h"),
        (3600, "1h"),
        (3601, "1h 1m"),
        (3660, "1h 1m"),
        (5400, "1h 30m"),
        (-30, "0m"),
    ],
)
def test_format_watch_time(seconds, expected):
    assert format_watch_time(seconds) == expected