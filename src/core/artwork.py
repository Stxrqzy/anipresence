"""Non-blocking, disk-cached TMDB enrichment; original AniList data is retained."""
import hashlib
import json
import queue
import threading
import time
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
        self.generation = 0
        self.cache = {}
        self.pending = set()
        self.lock = threading.RLock()
        self.jobs = queue.Queue(maxsize=2000)
        self.closed = False
        try:
            self.cache = json.loads((self.directory / "tmdb.json").read_text(encoding="utf-8"))
            if not isinstance(self.cache, dict):
                self.cache = {}
        except (OSError, ValueError):
            pass
        self.worker = None

    def configure(self, credential):
        with self.lock:
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

    def enrich(self, entry, episode=False):
        result = dict(entry)
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
                if poster and self.image_path(poster).exists():
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
            if detail is None and key not in self.pending:
                try:
                    self.jobs.put_nowait((self.generation, self.credential, key, dict(entry), ep))
                    self.pending.add(key)
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
            if path.exists():
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
            if job is None:
                return
            generation, credential, key, entry, ep = job
            if generation != self.generation:
                continue
            client = TMDBClient(credential)
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
                        self.pending.discard(key)
                if not self.closed and generation == self.generation:
                    self.ready()

    def _store(self, key, value, generation):
        with self.lock:
            if generation != self.generation or self.closed:
                return
            self.cache[key] = {"at": time.time(), "value": value}
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                (self.directory / "tmdb.json").write_text(json.dumps(self.cache), encoding="utf-8")
            except OSError:
                pass

    def close(self):
        self.closed = True
        try:
            self.jobs.put_nowait(None)
        except queue.Full:
            pass
