"""Stable anime identities across AniList and older title-only records."""
import re
import unicodedata
from collections import defaultdict


def normalized_title(title):
    return " ".join(unicodedata.normalize("NFKC", str(title or "")).casefold().split())


def media_id(entry):
    value = entry.get("media_id") or entry.get("anilist_id")
    if not value:
        for field in ("anilist_url", "url"):
            match = re.match(r"https?://(?:www\.)?anilist\.co/anime/(\d+)(?:/|$|[?#])", str(entry.get(field) or ""))
            if match:
                value = match[1]
                break
    try:
        return int(value) if int(value) > 0 else None
    except (ValueError, TypeError):
        return None


class MediaIdentity:
    def __init__(self, entries):
        self.names = defaultdict(set)
        self.display = {}
        self.english = {}
        self.title_names = {}
        for entry in entries:
            name = normalized_title(entry.get("title"))
            if name:
                self.title_names.setdefault(name, " ".join(str(entry["title"]).split()))
            identity = media_id(entry)
            if identity:
                for title in [entry.get("title"), entry.get("title_english"), *(entry.get("aliases") or [])]:
                    if normalized_title(title):
                        self.names[normalized_title(title)].add(identity)
                if entry.get("title"):
                    self.display[identity] = " ".join(entry["title"].split())
                if entry.get("title_english"):
                    self.english[identity] = entry["title_english"]

    def key(self, entry):
        identity = media_id(entry)
        name = normalized_title(entry.get("title"))
        matches = self.names.get(name, set())
        if identity or len(matches) == 1:
            return ("anilist", identity or next(iter(matches)))
        return ("title", name)

    def title(self, entry):
        key = self.key(entry)
        fallback = self.title_names.get(normalized_title(entry.get("title")), "Unknown")
        if key[0] == "anilist":
            return self.english.get(key[1]) or self.display.get(key[1], fallback)
        return entry.get("title_english") or fallback


def merge_title_rows(titles, references=()):
    entries = [dict(value, title=value.get("title") or name) for name, value in titles.items()]
    resolver = MediaIdentity([*entries, *references])
    rows = {}
    for entry in entries:
        key = resolver.key(entry)
        row = rows.setdefault(key, {"title": resolver.title(entry), "days": {}, "seconds": 0,
                                    "local_seconds": 0, "imported_seconds": 0, "last_watched": 0})
        local = max(0, entry.get("seconds", 0)-(entry.get("imported_seconds") or 0))
        row["local_seconds"] += local
        # Imports are snapshots of the same anime, not separate playback.
        row["imported_seconds"] = max(row["imported_seconds"], entry.get("imported_seconds") or 0)
        if entry.get("imported_progress") is not None:
            row["imported_progress"] = max(row.get("imported_progress", 0), entry["imported_progress"])
        for day, seconds in (entry.get("days") or {}).items():
            row["days"][day] = row["days"].get(day, 0)+seconds
        if key[0] == "anilist":
            row["media_id"] = key[1]
        newer = (entry.get("last_watched") or 0) >= row["last_watched"]
        for field in ("episode", "website", "url", "cover", "genres", "aliases", "season_year", "title_english"):
            if entry.get(field) and (newer or not row.get(field)):
                row[field] = entry[field]
        row["last_watched"] = max(row["last_watched"], entry.get("last_watched") or 0)
        row["seconds"] = row["local_seconds"]+row["imported_seconds"]
    return rows, resolver
