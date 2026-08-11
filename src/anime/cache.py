"""Disk-backed cache for AniList results.

Keyed by the normalized search string and expired after a configurable TTL so
repeated polls never hit the API for the same show.
"""
import json
import logging
import threading
import time
from pathlib import Path

logger = logging.getLogger("anipresence.cache")


class AniCache:
    def __init__(self, ttl_days=7, path=None):
        self._ttl = max(1, int(ttl_days)) * 86400
        self._path = Path(path) if path else Path.cwd() / "anilist_cache.json"
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self):
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self):
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._data, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:
            logger.debug("cache save failed: %s", exc)

    def get(self, key):
        """Return cached media dict if present and not expired, else None."""
        key = (key or "").strip()
        if not key:
            return None
        with self._lock:
            entry = self._data.get(key)
            if not entry:
                return None
            age = time.time() - entry.get("fetched_at", 0)
            if age > self._ttl:
                self._data.pop(key, None)
                return None
            return entry.get("media")

    def set(self, key, media_dict):
        key = (key or "").strip()
        if not key or not media_dict:
            return
        with self._lock:
            self._data[key] = {"media": media_dict, "fetched_at": time.time()}
            self._save()
