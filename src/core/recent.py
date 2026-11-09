"""Recently-watched history persisted as a small JSON file."""
import json
import logging
import threading
import time
from pathlib import Path

logger = logging.getLogger("anipresence.recent")


class RecentHistory:
    MAX_ITEMS = 1000

    def __init__(self, path=None):
        self._path = Path(path) if path else Path.cwd() / "recent.json"
        self._lock = threading.Lock()
        self._items = self._load()

    def _load(self):
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            return []

    def _save(self):
        try:
            self._path.write_text(
                json.dumps(self._items, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.debug("recent save failed: %s", exc)

    def items(self):
        with self._lock:
            return list(self._items)

    @staticmethod
    def _key_of(entry):
        return (
            str(entry.get("title", "")).lower(),
            entry.get("episode"),
            str(entry.get("website", "")).lower(),
        )

    def add(self, entry):
        """Insert or bump an entry, dedupe by (title, episode, website), cap size."""
        with self._lock:
            key = self._key_of(entry)
            carried = next(
                (i.get("watch_seconds", 0) for i in self._items
                 if self._key_of(i) == key),
                0,
            )
            self._items = [i for i in self._items if self._key_of(i) != key]
            entry = dict(entry)
            entry["last_watched"] = time.time()
            entry.setdefault("watch_seconds", carried)
            self._items.insert(0, entry)
            del self._items[self.MAX_ITEMS:]
            self._save()

    def add_watch_seconds(self, title, episode, website, seconds, playback=None):
        """Accumulate playback seconds onto the matching entry (or create one)."""
        if seconds <= 0:
            return
        with self._lock:
            key = (str(title).lower(), episode, str(website).lower())
            for i in self._items:
                if self._key_of(i) == key:
                    i["watch_seconds"] = i.get("watch_seconds", 0) + seconds
                    i["last_watched"] = time.time()
                    if playback:
                        i.update(playback)
                    self._save()
                    return
            entry = {
                "title": title,
                "episode": episode,
                "website": website,
                "watch_seconds": seconds,
                "last_watched": time.time(),
            }
            self._items.insert(0, entry)
            del self._items[self.MAX_ITEMS:]
            self._save()

    def remove(self, entry):
        """Delete the entry matching *entry*'s (title, episode, website) key."""
        key = self._key_of(entry)
        with self._lock:
            before = len(self._items)
            self._items = [i for i in self._items if self._key_of(i) != key]
            if len(self._items) != before:
                self._save()

    def clear(self):
        """Remove every entry."""
        with self._lock:
            if self._items:
                self._items = []
                self._save()
