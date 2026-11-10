"""Watch-time analytics: per-day and per-title playback totals.

The controller accumulates real playback seconds (see :func:`Controller
._accumulate_watch_time`) and rolls them into this log. Unlike
:class:`RecentHistory` (which keeps only the last N entries), the log keeps a
running per-day total and per-title totals, so the dashboard can render the
watch-time streak and a monthly hours chart.
"""
import json
import copy
import logging
import threading
import time
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger("anipresence.watchlog")


def account_slug(username):
    """Filesystem-safe identifier for an AniList account name."""
    name = "".join(c if c.isalnum() else "_" for c in (username or "").lower()).strip("_")
    return name or "account"


def watchlog_path_for(username, app_dir=None):
    """Per-account save file for *username* (empty username -> shared default)."""
    base = Path(app_dir) if app_dir else Path.cwd()
    if not (username or "").strip():
        return base / "watchlog.json"
    return base / f"watchlog-{account_slug(username)}.json"


class WatchLog:
    def __init__(self, path=None):
        self._path = Path(path) if path else Path.cwd() / "watchlog.json"
        self._lock = threading.Lock()
        self._days = {}      # "YYYY-MM-DD" -> seconds watched that day
        self._titles = {}    # title -> dict(seconds, episode, website, url, last_watched)
        self._sections = {}  # AniList list label ("Watching"/"Planning"/...) -> entries
        self._activity = {}
        self._episodes = {}
        self._load()

    def _load(self):
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._days = {k: float(v) for k, v in data.get("days", {}).items()}
                self._titles = data.get("titles", {})
                self._activity = data.get("activity") or {}
                self._episodes = data.get("local_episodes") or {}
                self._sections = {
                    k: list(v) for k, v in (data.get("sections", {}) or {}).items()
                }
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            self._days, self._titles, self._sections = {}, {}, {}

    def _save(self):
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(
                    {
                        "days": self._days,
                        "titles": self._titles,
                        "sections": self._sections,
                        "activity": self._activity,
                        "local_episodes": self._episodes,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.debug("watchlog save failed: %s", exc)

    def add(self, title, episode, website, url, cover, seconds, media_id=None):
        """Roll *seconds* into today's total and the per-title total."""
        if seconds <= 0 or not title:
            return
        day = date.today().isoformat()
        title = str(title)
        with self._lock:
            self._days[day] = self._days.get(day, 0.0) + seconds
            entry = self._titles.setdefault(
                title,
                {
                    "seconds": 0.0,
                    "episode": episode,
                    "website": website,
                    "url": url,
                    "cover": cover,
                    "days": {},  # per-day seconds so removals stay consistent
                },
            )
            entry["seconds"] = entry.get("seconds", 0.0) + seconds
            entry_days = entry.setdefault("days", {})
            entry_days[day] = entry_days.get(day, 0.0) + seconds
            if episode:
                entry["episode"] = episode
            if website:
                entry["website"] = website
            if url:
                entry["url"] = url
            if cover:
                entry["cover"] = cover
            entry["last_watched"] = time.time()
            if media_id:
                entry["media_id"] = int(media_id)
            if episode:
                key = f"{media_id or title.casefold()}:{episode}"
                playback = self._episodes.setdefault(key, {"title": title, "media_id": media_id,
                                                           "episode": episode, "days": {}})
                playback["days"][day] = playback["days"].get(day, 0)+seconds
            self._save()

    def remove_title(self, title):
        """Drop one title and subtract its seconds from the daily totals."""
        title = str(title)
        with self._lock:
            entry = self._titles.pop(title, None)
            self._episodes = {k: e for k, e in self._episodes.items() if e.get("title") != title}
            media_id = entry.get("media_id") if entry else None
            self._activity["items"] = [e for e in self._activity.get("items", [])
                if not ((media_id and (e.get("media") or {}).get("id") == media_id)
                        or title in ((e.get("media") or {}).get("title") or {}).values())]
            if entry is None:
                self._save()
                return
            for day, seconds in (entry.get("days") or {}).items():
                remaining = self._days.get(day, 0.0) - seconds
                if remaining > 0:
                    self._days[day] = remaining
                else:
                    self._days.pop(day, None)
            self._save()

    def import_titles(self, entries):
        """Seed per-title totals from external history (e.g. AniList import).

        Idempotent: each call replaces the previous import's *imported* seconds
        (tracked separately per title), so re-importing the same list never
        double-counts and locally-watched time on the same title is untouched.
        *entries* is a list of dicts with ``title``/``seconds`` (and optional
        episode/website/url/cover). Imported seconds count toward the per-title
        totals and the grand total, but never toward the daily chart or the
        streak — those stay a record of what you actually watched in the app.
        """
        entries = list(entries or [])
        imported = 0
        with self._lock:
            # 1) Roll back every previous import's contribution exactly, leaving
            #    real watch time recorded by add() alone.
            for entry in self._titles.values():
                prev = float(entry.pop("imported_seconds", 0.0) or 0.0)
                if prev:
                    entry["seconds"] = max(0.0, entry.get("seconds", 0.0) - prev)
                entry.pop("source", None)
                entry.pop("imported_progress", None)
            # 2) Seed the fresh snapshot.
            for item in entries:
                title = str(item.get("title") or "").strip()
                seconds = float(item.get("seconds") or 0)
                if seconds <= 0 or not title:
                    continue
                entry = self._titles.setdefault(title, {"seconds": 0.0, "days": {}})
                entry["seconds"] = entry.get("seconds", 0.0) + seconds
                entry["imported_seconds"] = float(entry.get("imported_seconds", 0.0)) + seconds
                entry["source"] = "anilist"
                if item.get("media_id"):
                    entry["media_id"] = int(item["media_id"])
                if item.get("episode"):
                    entry["episode"] = item["episode"]
                    entry["imported_progress"] = item["episode"]
                if item.get("website"):
                    entry["website"] = item["website"]
                if item.get("url"):
                    entry["url"] = item["url"]
                if item.get("cover"):
                    entry["cover"] = item["cover"]
                entry.setdefault("last_watched", time.time())
                imported += seconds
            # 3) Titles with no watch time left at all (never watched locally,
            #    and no longer listed) disappear from the stats.
            for title in [t for t, e in self._titles.items()
                          if e.get("seconds", 0.0) <= 0 and not (e.get("days") or {})]:
                del self._titles[title]
            self._save()
        return imported

    def set_anilist_sections(self, sections):
        """Persist the imported AniList lists (label -> list of entry dicts)."""
        with self._lock:
            self._sections = {k: list(v) for k, v in (sections or {}).items()}
            self._save()

    def anilist_sections(self):
        with self._lock:
            return {k: list(v) for k, v in self._sections.items()}

    def clear(self):
        """Reset all watch-time data."""
        with self._lock:
            self._days = {}
            self._titles = {}
            self._activity = {}
            self._episodes = {}
            self._save()

    def set_activity(self, snapshot):
        with self._lock:
            self._activity = copy.deepcopy(snapshot)
            self._save()

    def activity(self):
        with self._lock:
            return copy.deepcopy(self._activity)

    def analytics(self):
        from .activity_stats import reconcile
        with self._lock:
            values = copy.deepcopy((self._titles, self._days, self._episodes, self._activity))
        return reconcile(*values)

    # ------------------------------------------------------------ read side
    def days(self):
        with self._lock:
            return dict(self._days)

    def titles(self):
        with self._lock:
            return {k: dict(v) for k, v in self._titles.items()}

    def total_seconds(self):
        with self._lock:
            return sum(t.get("seconds", 0.0) for t in self._titles.values())

    # ------------------------------------------------------------ analytics
    def current_streak(self):
        """Consecutive days (ending today or yesterday) with watch time."""
        days = self.days()
        today = date.today()
        if days.get(today.isoformat(), 0) <= 0:
            today -= timedelta(days=1)
        streak = 0
        while days.get(today.isoformat(), 0) > 0:
            streak += 1
            today -= timedelta(days=1)
        return streak

    def last_n_days(self, n=30):
        """Earliest-to-latest (date, seconds) pairs for the last *n* days."""
        days = self.days()
        out = []
        for i in range(n - 1, -1, -1):
            day = (date.today() - timedelta(days=i)).isoformat()
            out.append((day, days.get(day, 0.0)))
        return out

    def top_titles(self, limit=10):
        """Most-watched titles, each carrying resume info (url/episode)."""
        titles = self.titles()
        ordered = sorted(
            titles.items(), key=lambda kv: kv[1].get("seconds", 0), reverse=True
        )
        return [{"title": name, **data} for name, data in ordered[:limit]]

    def continue_list(self, limit=8):
        """Titles you can resume, most recently watched first.

        Each entry carries title/episode/url/website/cover so the UI can offer a
        'Resume Ep N' jump straight back to the last spot. Entries without a
        resumable URL are skipped.
        """
        titles = self.titles()
        ordered = sorted(
            (t for t in titles.items() if t[1].get("url")),
            key=lambda kv: kv[1].get("last_watched") or 0,
            reverse=True,
        )
        return [{"title": name, **data} for name, data in ordered[:limit]]


def format_hours(seconds):
    """Format seconds as a compact hours/minutes label."""
    total_min = int(round(seconds / 60))
    hours, minutes = divmod(total_min, 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"
