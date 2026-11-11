"""Non-blocking, disk-cached TMDB enrichment; original AniList data is retained."""
import hashlib
import json
import queue
import threading
import time
import itertools
from pathlib import Path
from urllib.parse import urlsplit
from PySide6.QtGui import QImage
from ..anime.tmdb import TMDBClient
from .media_identity import media_id, normalized_title


class ArtworkService:
    def __init__(self, directory, ready):
        self.directory = Path(directory)
        self.ready = ready
        self.credential = ""
        self.cover_source = "tmdb"
        self.generation = 0
        self.cache = {}
        self.api_cache = {}
        self.pending = {}
        self.sequence = itertools.count()
        self.lock = threading.RLock()
        self.jobs = queue.PriorityQueue(maxsize=2500)
        self.closed = False
        try:
            saved = json.loads((self.directory / "tmdb.json").read_text(encoding="utf-8"))
            if isinstance(saved, dict) and saved.get("version") == 2:
                self.cache = saved.get("entries") or {}
                self.api_cache = saved.get("responses") or {}
        except (OSError, ValueError):
            pass
        self.worker = None
        self._saved_at = 0

    def configure(self, credential, cover_source=None):
        with self.lock:
            if cover_source in ("tmdb", "anilist"):
                self.cover_source = cover_source
            if credential.strip() != self.credential:
                self.credential = credential.strip()
                self.generation += 1
                self.pending.clear()
                # Retry previous failures when credentials change.
                self.cache = {k: v for k, v in self.cache.items() if v.get("value")}

    @staticmethod
    def key(entry):
        identity = media_id(entry)
        return f"anime:{identity}" if identity else "title:" + normalized_title(entry.get("title"))

    def image_path(self, url):
        ext = Path(urlsplit(url).path).suffix.lower() or ".jpg"
        return self.directory / "covers" / (hashlib.sha1(url.encode("utf-8", "ignore")).hexdigest()[:20] + ext)

    def _cached(self, key):
        item = self.cache.get(key)
        if item and time.time()-item.get("at", 0) < (7*86400 if item.get("value") else 300):
            return item.get("value", {})
        return None

    def enrich(self, entry, episode=False, priority=20, request=True):
        result = dict(entry)
        if self.cover_source == "anilist" and entry.get("anilist_cover"):
            result["cover"] = entry["anilist_cover"]
            if "cover_url" in result:
                result["cover_url"] = result["cover_extra_url"] = entry["anilist_cover"]
        if entry.get("title_english"):
            result["title"] = entry["title_english"]
        if not self.credential or not entry.get("title") or self.closed:
            return result
        show_key = self.key(entry)
        ep = entry.get("episode") if episode else None
        key = f"{show_key}:episode:{ep}" if ep else show_key
        with self.lock:
            show = self._cached(show_key)
            detail = self._cached(key)
            for data in (show or {}, detail or {}):
                poster = data.get("poster")
                if self.cover_source == "tmdb" and poster and self.image_path(poster).exists():
                    result["anilist_cover"] = entry.get("anilist_cover") or entry.get("cover") or entry.get("cover_url") or ""
                    result["cover"] = poster
                    if "cover_url" in result:
                        result["cover_url"] = poster
                        result["cover_extra_url"] = poster
                for field in ("episode_art", "backdrop"):
                    if data.get(field) and self.image_path(data[field]).exists():
                        result[field] = data[field]
                if not result.get("episode_title") and data.get("episode_title"):
                    result["episode_title"] = data["episode_title"]
                if data.get("title_english") and not entry.get("title_english"):
                    result["title"] = data["title_english"]
            if request and (detail is None or show is None) and priority < self.pending.get(key, 9999):
                try:
                    self.jobs.put_nowait((priority, next(self.sequence), self.generation, self.credential, key, dict(entry), ep))
                    self.pending[key] = priority
                    if self.worker is None:
                        self.worker = threading.Thread(target=self._run, daemon=True, name="tmdb-artwork")
                        self.worker.start()
                except queue.Full:
                    pass
        return result

    def _download(self, client, data):
        for field in ("poster", "backdrop", "episode_art"):
            url = data.get(field)
            if not url:
                continue
            path = self.image_path(url)
            if path.exists() and not QImage(str(path)).isNull():
                continue
            try:
                response = client.session.get(url, timeout=10)
                response.raise_for_status()
                if QImage.fromData(response.content).isNull():
                    raise ValueError("Invalid image")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(response.content)
            except (OSError, ValueError):
                data.pop(field, None)

    def _run(self):
        while not self.closed:
            job = self.jobs.get()
            if len(job) == 3:
                return
            priority, _, generation, credential, key, entry, ep = job
            if generation != self.generation or self.pending.get(key) != priority:
                continue
            client = TMDBClient(credential)
            client.response_cache = self.api_cache
            show = None
            try:
                show_key = self.key(entry)
                with self.lock:
                    show = self._cached(show_key)
                if show is None:
                    show = client.match(entry)
                    self._download(client, show)
                    self._store(show_key, show, generation)
                value = client.episode(show, ep) if ep and show else (show or {})
                self._download(client, value)
                self._store(key, value, generation)
            except Exception:
                # Never log requests exceptions: their URL can contain an API key.
                if show is None:
                    self._store(self.key(entry), {}, generation)
                self._store(key, {}, generation)
            finally:
                client.session.close()
                with self.lock:
                    if generation == self.generation:
                        self.pending.pop(key, None)
                if not self.closed and generation == self.generation:
                    self.ready()

    def _store(self, key, value, generation):
        with self.lock:
            if generation != self.generation or self.closed:
                return
            self.cache[key] = {"at": time.time(), "value": value}
            if time.time()-self._saved_at < 2 and len(self.pending) > 1:
                return
            snapshot = {"version": 2, "entries": dict(self.cache), "responses": dict(self.api_cache)}
        # Serialization and disk I/O must not hold the lock used by visible rows.
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / "tmdb.json.tmp"
            path.write_text(json.dumps(snapshot), encoding="utf-8")
            with self.lock:
                if generation == self.generation and not self.closed:
                    path.replace(self.directory / "tmdb.json")
                    self._saved_at = time.time()
        except OSError:
            pass

    def close(self):
        self.closed = True
        try:
            self.jobs.put_nowait((-1, next(self.sequence), None))
        except queue.Full:
            pass
