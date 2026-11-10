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
        self._history_cleared_before = 0
        self._history_dismissed = []
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
                self._history_cleared_before = data.get("history_cleared_before") or 0
                self._history_dismissed = data.get("history_dismissed") or []
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
                        "history_cleared_before": self._history_cleared_before,
                        "history_dismissed": self._history_dismissed,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.debug("watchlog save failed: %s", exc)

    def _title_storage_key(self, title, identity):
        """Keep distinct explicit IDs even when AniList gives them the same title."""
        from .media_identity import media_id
        existing = self._titles.get(title, {})
        if identity and media_id(existing) and media_id(existing) != int(identity):
            return f"{title} [AniList {identity}]"
        return title

    def add(self, title, episode, website, url, cover, seconds, media_id=None):
        """Roll *seconds* into today's total and the per-title total."""
        if seconds <= 0 or not title:
            return
        day = date.today().isoformat()
        title = str(title)
        with self._lock:
            self._days[day] = self._days.get(day, 0.0) + seconds
            entry = self._titles.setdefault(
                self._title_storage_key(title, media_id),
                {
                    "title": title,
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
        target = title if isinstance(title, dict) else {"title": str(title)}
        with self._lock:
            from .activity_stats import feed_episodes
            from .media_identity import merge_title_rows
            references = [e for entries in self._sections.values() for e in entries]
            _, resolver = merge_title_rows(self._titles, [*references, *feed_episodes(self._activity)])
            key = resolver.key(target)
            removed = [name for name, value in self._titles.items()
                       if resolver.key(dict(value, title=value.get("title") or name)) == key]
            self._episodes = {k: e for k, e in self._episodes.items() if resolver.key(e) != key}
            self._activity["items"] = [e for e in self._activity.get("items", [])
                if resolver.key({"media_id": (e.get("media") or {}).get("id"),
                                 "title": ((e.get("media") or {}).get("title") or {}).get("romaji")}) != key]
            for name in removed:
                entry = self._titles.pop(name)
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
        from .media_identity import MediaIdentity, media_id
        entries = list(entries or [])
        resolver = MediaIdentity(entries)
        unique = {}
        for item in entries:
            key = resolver.key(item)
            if key not in unique or float(item.get("seconds") or 0) > float(unique[key].get("seconds") or 0):
                unique[key] = item
        entries = list(unique.values())
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
                identity = media_id(item)
                entry = self._titles.setdefault(self._title_storage_key(title, identity),
                                                {"title": title, "seconds": 0.0, "days": {}})
                entry["seconds"] = entry.get("seconds", 0.0) + seconds
                entry["imported_seconds"] = float(entry.get("imported_seconds", 0.0)) + seconds
                entry["source"] = "anilist"
                if identity:
                    entry["media_id"] = identity
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
            references = [e for entries in self._sections.values() for e in entries]
            values = copy.deepcopy((self._titles, self._days, self._episodes, self._activity, references))
        return reconcile(*values)

    def history(self, local):
        from .history import combined_history
        with self._lock:
            references = [dict(value, title=value.get("title") or name) for name, value in self._titles.items()]
            references.extend(e for entries in self._sections.values() for e in entries)
            snapshot, references, dismissed = copy.deepcopy((self._activity, references, self._history_dismissed))
            cutoff = self._history_cleared_before
        return combined_history(local, snapshot, references, cutoff, dismissed)

    def clear_feed_history(self):
        with self._lock:
            self._history_cleared_before = time.time()
            self._history_dismissed = []
            self._save()

    def dismiss_history(self, entry):
        key = entry.get("history_key")
        if key:
            with self._lock:
                if key not in self._history_dismissed:
                    self._history_dismissed.append(key)
                self._save()

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
        from .media_identity import merge_title_rows
        rows, _ = merge_title_rows(self.titles())
        return sorted(rows.values(), key=lambda e: e.get("seconds", 0), reverse=True)[:limit]

    def continue_list(self, limit=8):
        """Titles you can resume, most recently watched first.

        Each entry carries title/episode/url/website/cover so the UI can offer a
        'Resume Ep N' jump straight back to the last spot. Entries without a
        resumable URL are skipped.
        """
        from .media_identity import merge_title_rows
        rows, _ = merge_title_rows(self.titles())
        ordered = sorted(
            (row for row in rows.values() if row.get("url")),
            key=lambda row: row.get("last_watched") or 0,
            reverse=True,
        )
        return ordered[:limit]


def format_hours(seconds):
    """Format seconds as a compact hours/minutes label."""
    total_min = int(round(seconds / 60))
    hours, minutes = divmod(total_min, 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"
