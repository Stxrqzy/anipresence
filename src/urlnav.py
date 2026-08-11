"""Small helpers for navigating episode URLs."""
import re

_EP_RE = re.compile(r"(?:episode|ep)[\-_/:=]?\s*(\d+)", re.IGNORECASE)
_TRAILING_NUM = re.compile(r"/(\d+)/?$")


def increment_episode_url(url):
    """Return *url* with the last episode number incremented, or ``None``.

    Recognises the common patterns ``episode-5``, ``ep/5``, ``ep=5``, ``episode5``
    and a bare trailing numeric path segment (``/watch/5``). Falls back to the
    last numeric path segment so it still works on most fansub hosts.
    """
    if not url:
        return None
    match = None
    for m in _EP_RE.finditer(url):
        match = m  # last match wins (avoids a season number earlier in the path)
    if match is None:
        match = _TRAILING_NUM.search(url)
    if match is None:
        return None
    try:
        n = int(match.group(1))
    except (TypeError, ValueError):
        return None
    start, end = match.span(1)
    return url[:start] + str(n + 1) + url[end:]


def format_watch_time(seconds):
    """Human-readable duration: ``75m``, ``3h 40m`` (rounds up to a minute)."""
    seconds = max(0, int(seconds))
    total_minutes = (seconds + 59) // 60
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"