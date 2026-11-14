"""Core controller: the glue between Chrome detection, AniList and Discord.

Runs inside a :class:`QThread` (see :class:`ControllerWorker`). Each poll tick:

  1. Ensure Chrome's debugging endpoint is reachable (auto-launch if configured).
  2. Discover page + iframe targets via CDP.
  3. Evaluate JS in each page (and its child iframes) to read the real video
     player state, visibility and focus.
  4. Pick the "best" tab (visible/focused/playing).
  5. Run the site detectors to extract {title, episode}.
  6. Resolve the title against AniList (with disk cache + fuzzy matching).
  7. Push the result to Discord Rich Presence and emit UI signals.

Throttling: the Discord presence is only re-sent when meaningful things change
or the remaining time drifts by >= 15s, so we don't hammer the RPC pipe.
"""
import json
import logging
import secrets
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from pypresence import ActivityType, StatusDisplayType

from ..config import APP_DIR
from .. import __version__
from ..anime.anilist import AniListClient, AniListError, Media
from ..anime.cache import AniCache
from ..anime.matcher import best_match
from ..anime.titles import (
    format_episode_progress, next_airing_label, normalize_search, search_terms,
)
from ..browser.cdp import ChromeClient, ChromeError
from ..browser.detectors import DetectorRegistry, PageContext, VideoState
from ..browser.extension import ExtensionBridge, ensure_token
from ..core.recent import RecentHistory
from ..core.watchlog import WatchLog, format_hours, watchlog_path_for
from ..discord.rpc import DiscordClient
from ..discord.vencord import VencordBridge
from ..ui.bridge import QtLogHandler

logger = logging.getLogger("anipresence.controller")

# How long to skip re-querying AniList for a search key that returned no match
# (in seconds). Prevents poll-tick loops from hammering the rate limit when a
# page has a video but an empty/no-match title (e.g. a site home page).
MISS_TTL = 300.0

# JS evaluated in every page target: real video state + page metadata.
PAGE_INFO_JS = r"""(() => {
  const v = document.querySelector('video');
  // Keep this reader in sync with browser-extension/content.js.
  function readAniPmEpisode() {
    if (!/(^|\.)ani\.pm$/i.test(location.hostname)) return null;
    const ordinal = document.querySelector('.ytw-title__n');
    const heading = document.querySelector('h1.ytw-title');
    const current = document.querySelector(
      '[data-episode-number].is-current, [data-episode-number][aria-current="true"], ' +
      '[data-episode-number]:has([aria-current="true"])'
    );
    // The heading remains mounted even when the playlist is filtered/virtualized.
    const text = ordinal ? ordinal.textContent.trim().replace(/\.$/, '') : '';
    const match = heading && heading.textContent.match(/^Episode\s+(\d{1,4})\b/i);
    const raw = text || (match && match[1]) ||
      (current && current.getAttribute('data-episode-number'));
    if (!/^\d{1,4}$/.test(raw || '')) return null;
    const episode = Number(raw);
    return episode > 0 ? episode : null;
  }
  function readAniPmEpisodeTitle() {
    if (!/(^|\.)ani\.pm$/i.test(location.hostname)) return '';
    const heading = document.querySelector('h1.ytw-title');
    const ordinal = document.querySelector('.ytw-title__n');
    // ani.pm includes an ordinal only for a named episode. Plain "Episode N"
    // headings and movie/series titles must not become episode names.
    if (!heading || !ordinal) return '';
    const text = heading.textContent.trim();
    const prefix = ordinal.textContent.trim();
    if (!prefix || !text.startsWith(prefix)) return '';
    return text.slice(prefix.length).replace(/\s+/g, ' ').trim().slice(0, 128);
  }
  return {
    title: document.title || '',
    episode: readAniPmEpisode(),
    episodeTitle: readAniPmEpisodeTitle(),
    visibility: document.visibilityState || '',
    focused: document.hasFocus ? document.hasFocus() : false,
    video: v ? {
      hasVideo: true,
      currentTime: Number(v.currentTime) || 0,
      duration: (Number.isFinite(v.duration) && v.duration > 0) ? v.duration : 0,
      paused: !!v.paused,
      ended: !!v.ended,
      readyState: v.readyState || 0,
      src: (v.currentSrc || v.src || '').slice(0, 300)
    } : { hasVideo: false }
  };
})()"""

# Crunchyroll embeds a JSON blob with episode/series metadata in the page.
NEXT_DATA_JS = "typeof __NEXT_DATA__ !== 'undefined' ? JSON.stringify(__NEXT_DATA__).slice(0, 3000000) : null"


@dataclass
class Candidate:
    """A page target plus its evaluated state."""
    target: object
    info: dict
    video: VideoState


class _ExtTarget:
    """Minimal stand-in for a CDP target when a normal browser tab reports via
    the extension bridge (CDP needs id/url; that is all the pipeline touches)."""
    __slots__ = ("id", "url", "title")

    def __init__(self, target_id, url, title=""):
        self.id = target_id
        self.url = url
        self.title = title


@dataclass
class CurrentSession:
    """Everything known about the anime currently being watched."""
    detection: object
    media: object          # Media | None
    confidence: float
    session_start: float
    key: str               # stable identity for this title+episode+site


class Controller:
    def __init__(self, cfg, bridge):
        self.cfg = cfg                    # ConfigManager
        self.bridge = bridge
        self.stop_event = threading.Event()

        port = cfg.data.chrome_debugging_port
        self.chrome = ChromeClient(
            port=port,
            auto_launch=cfg.data.chrome_auto_launch,
            chrome_path=cfg.data.chrome_path,
            browser=cfg.data.browser,
        )
        self.registry = DetectorRegistry(custom_sites=cfg.data.custom_sites)
        self.anilist = AniListClient(access_token=cfg.data.anilist_access_token)
        self.cache = AniCache(ttl_days=cfg.data.anilist_cache_ttl_days, path=APP_DIR / "anilist_cache.json")
        self.discord = DiscordClient()
        self.vencord = VencordBridge()
        self.recent = RecentHistory(path=APP_DIR / "recent.json")
        self._accounts = list(cfg.data.anilist_accounts or [])
        self._active_account = (cfg.data.anilist_username or "").strip()
        if self._active_account and self._active_account not in self._accounts:
            self._accounts.append(self._active_account)
            cfg.set(anilist_accounts=self._accounts)
        self.watchlog = self._make_watchlog()
        from .artwork import ArtworkService
        self.artwork = ArtworkService(APP_DIR, self.bridge.artwork_ready.emit)
        self.artwork.configure(cfg.data.tmdb_credential, cfg.data.cover_source)

        # Localhost bridge the browser extension reports into. The token is
        # generated once per install and persisted so the extension can be
        # pointed at the same value after re-installs.
        if not cfg.data.extension_token:
            cfg.set(extension_token=ensure_token(cfg.data.extension_token))
        self.extension = ExtensionBridge(
            port=cfg.data.extension_port, token=cfg.data.extension_token
        )
        self.extension.now_provider = self.now_playing

        self._anilist_miss = {}                  # search key -> last-failed timestamp
        self._match_prompted = {}                # url -> last time we offered candidates
        # internal state
        self._current = None                 # CurrentSession | None
        self._next_data_cache = {}           # target_id -> (url, json_str)
        self._last_sent = None               # dict(sig, end, at)
        self._last_discord_state = (None, None)
        self._last_chrome_state = (None, None)
        self._last_discord_attempt = 0.0
        self._last_recent_key = None
        self._force_reconnect = False
        self._discord_warned = False
        self._launch_attempted = False
        self._chrome_was_up = False
        self._last_chrome_launch = 0.0
        self._log_handler_installed = False
        self._manual = None                        # manual-presence dict | None
        self._watch_accum = {}                     # key -> seconds since last flush
        self._watch_progress = {}                  # key -> last video.current_time
        self._watch_meta = {}                      # key -> (title, episode, website, url, cover)
        self._anilist_sync_inflight = set()
        self._anilist_synced = set()
        self._anilist_sync_retry = {}
        self._anilist_sync_lock = threading.Lock()
        self._anilist_status_lock = threading.RLock()
        self._anilist_pending = {}
        self._anilist_pending_errors = {}
        self._anilist_pull_inflight = False
        self._anilist_pull_token = None
        self._anilist_pull_again = False
        self._activity_inflight = set()
        self._activity_attempts = {}
        self._anilist_sync_error = ""
        self._anilist_sync_activity = "Ready"
        self._last_recent_emit = 0.0

    def set_custom_sites(self, sites):
        """Replace the user-defined anime-site list and sync the detectors.

        Persists the cleaned list (dropping empty name/host entries) and makes
        the running detection registry pick it up immediately.
        """
        cleaned = []
        for entry in sites or []:
            name = str(entry.get("name") or "").strip()
            host = str(entry.get("host") or "").strip()
            if name and host:
                cleaned.append({"name": name, "host": host})
        self.registry.set_custom_sites(cleaned)
        self.cfg.set(custom_sites=cleaned)
        return cleaned

    # ------------------------------------------------------------------ run
    def run(self):
        """Main loop - runs on the worker QThread."""
        self._install_log_bridge()
        self.bridge.log.emit(
            f"AniPresence started. Looking for Chrome on port "
            f"{self.cfg.data.chrome_debugging_port}..."
        )
        if self.cfg.data.extension_enabled:
            self.extension.start()
            if self.extension.running:
                # A configured port of 0 means "OS-assigned ephemeral", so the
                # bridge actually listens on the bound_port. Persist that real
                # port so restarts reuse it and settings.json stays stable.
                bound = self.extension.bound_port
                if self.cfg.data.extension_port in (0, None, "") and bound:
                    self.cfg.set(extension_port=bound)
                self.bridge.log.emit(
                    f"Browser-extension bridge listening on 127.0.0.1:{bound} "
                    f"(token: {self.extension.token})."
                )
                self._provision_extension_config()
            else:
                self.bridge.log.emit(
                    f"Browser-extension bridge could not open port {self.extension.port}."
                )
        # Keep the Stats tab and Library in sync with the user's AniList list on
        # every launch (idempotent: re-syncing replaces, never double-adds). With
        # a token set this pulls the authenticated account's own (possibly
        # private) list; otherwise it falls back to the public username.
        username = self._active_account or (self.cfg.data.anilist_username or "").strip()
        if username or (self.cfg.data.anilist_access_token or "").strip():
            self.bridge.log.emit("Syncing AniList list (two-way)…")
            self.sync_anilist(username)
        while not self.stop_event.is_set():
            try:
                self._tick()
            except ChromeError as exc:
                self._set_chrome_status(False, str(exc))
                self._no_video()
            except Exception:  # noqa: BLE001 - keep the loop alive at all costs
                logger.exception("unhandled error in poll tick")
            self.stop_event.wait(self.cfg.data.poll_interval_seconds)
        self._shutdown()

    def _provision_extension_config(self):
        """Pre-fill the extension's settings.json with port + token so loading
        it unpacked needs zero manual setup. Writes to every location the
        extension folder may live in (repo checkout, dist, installed {app}) so
        no matter which copy is loaded it gets the current token."""
        import sys
        from pathlib import Path

        candidates = []
        if getattr(sys, "frozen", False):
            candidates.append(Path(sys.executable).parent / "extension")
            meipass = getattr(sys, "_MEIPASS", None)
            if meipass:
                candidates.append(Path(meipass) / "extension")
        else:
            candidates.append(Path(__file__).resolve().parents[2] / "browser-extension")

        payload = json.dumps(
            {"bridgePort": self.extension.bound_port, "bridgeToken": self.extension.token},
            indent=2,
        )
        written = []
        for folder in candidates:
            try:
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "settings.json").write_text(payload, encoding="utf-8")
                written.append(str(folder))
            except OSError as exc:
                logger.warning("could not write extension settings into %s: %s", folder, exc)
        if written:
            self.bridge.log.emit(f"Pre-filled extension settings: {', '.join(written)}")

    def _shutdown(self):
        if self._current is not None:
            self._flush_watch(self._current.key)
        self._clear_rpc()
        try:
            self.extension.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.discord.close()
        except Exception:  # noqa: BLE001
            pass

    def _install_log_bridge(self):
        if self._log_handler_installed:
            return
        logging.getLogger().addHandler(QtLogHandler(self.bridge))
        self._log_handler_installed = True

    # ------------------------------------------------------------------ tick
    def _tick(self):
        self._retry_pending_episodes()
        # Reconnect to Discord opportunistically (throttled) when enabled. A
        # vencord-only broadcast never touches the local pipe.
        if (
            self.cfg.data.rpc_enabled
            and self.cfg.data.discord_application_id
            and self.cfg.data.rpc_broadcast != "vencord"
        ):
            if not self.discord.is_connected and time.time() - self._last_discord_attempt > 20:
                self._last_discord_attempt = time.time()
                try:
                    self._connect_discord()
                except Exception as exc:  # noqa: BLE001
                    if not self._discord_warned:
                        self.bridge.log.emit(f"Discord unavailable: {exc}")
                        self._discord_warned = True
                    self._set_discord_status(False, str(exc))

        self._accumulate_watch_time()
        if self._manual:
            # Manual mode: broadcast whatever the user typed and skip detection.
            self._sync_manual()
            self._set_chrome_status(self.chrome.is_available(), "Manual presence")
            return

        if not self.cfg.data.detection_enabled:
            self._set_chrome_status(self.chrome.is_available(), "Detection paused")
            return

        # Prefer the browser extension: it works with a normally-launched
        # browser, so any tab the user opened counts (no debug-port relaunch).
        ext = self.extension.live_tabs()
        if ext:
            self._tick_extension(ext)
            return

        if not self._ensure_chrome():
            return

        targets = self.chrome.discover()
        if not targets:
            self._set_chrome_status(False, "Chrome connected but no tabs")
            self._no_video()
            return

        pages = [t for t in targets if t.type == "page"]
        iframes = [t for t in targets if t.type == "iframe"]

        candidates = self._gather_pages(pages, iframes)
        best = self._choose_best(candidates)

        self._set_chrome_status(True, f"Connected · {len(pages)} tab(s)")

        if best is None:
            self._no_video()
            return

        ctx = self._build_context(best)
        detection = self.registry.detect(ctx) if ctx else None
        if detection is None:
            logger.debug("no detection on %s", best.target.url)
            self._no_anime()
            return

        detection.video = best.video
        self._apply_manual_override(detection)
        media, confidence = self._resolve_anime(detection)
        self._update_session(detection, media, confidence)

    def _tick_extension(self, ext):
        """Detection from the browser-extension bridge (normal browser tabs)."""
        candidates = []
        for tab_key, payload in ext:
            video_raw = payload.get("video") or {}
            candidates.append(
                Candidate(
                    _ExtTarget(str(tab_key), payload.get("url") or ""),
                    {
                        "title": payload.get("title") or "",
                        "episode": payload.get("episode"),
                        "episodeTitle": payload.get("episodeTitle"),
                        "visibility": payload.get("visibility") or "visible",
                        "focused": bool(payload.get("focused", True)),
                    },
                    _to_video_state(video_raw),
                )
            )

        self._set_chrome_status(True, f"Extension · {len(candidates)} tab(s)")
        best = self._choose_best(candidates)
        if best is None:
            self._no_video()
            return

        ctx = self._build_context(best)
        detection = self.registry.detect(ctx) if ctx else None
        if detection is None:
            logger.debug("no detection on extension tab %s", best.target.url)
            self._no_anime()
            return

        detection.video = best.video
        self._apply_manual_override(detection)
        media, confidence = self._resolve_anime(detection)
        self._update_session(detection, media, confidence)

    # ---------------------------------------------------------------- chrome
    # Cooldown between automatic Chrome relaunches (seconds). Chrome dying is
    # usually a crash/quit; giving it a breather avoids a launch loop, while a
    # shorter delay than the launch gate keeps recovery snappy.
    CHROME_RELAUNCH_COOLDOWN = 30.0

    def _ensure_chrome(self):
        if self.chrome.is_available():
            self._chrome_was_up = True
            self._last_chrome_launch = 0.0
            return True
        self._set_chrome_status(False, "Chrome not connected")
        self._no_video()
        if not self.cfg.data.chrome_auto_launch:
            return False
        # First launch (or manual "Launch Chrome") happens immediately; after
        # that, only relaunch when the browser was actually up and has dropped,
        # and not more often than the cooldown allows.
        now = time.time()
        gate = self._launch_attempted and not self._chrome_was_up
        if gate:
            return False
        if now - self._last_chrome_launch < self.CHROME_RELAUNCH_COOLDOWN:
            return False
        self._last_chrome_launch = now
        self._launch_attempted = True
        self._chrome_was_up = False
        self.bridge.log.emit("Debugging port closed. Relaunching Chrome with remote debugging...")
        try:
            message = self.chrome.launch()
            self.bridge.log.emit(message)
        except ChromeError as exc:
            self.bridge.log.emit(f"Chrome launch failed: {exc}")
        time.sleep(5)  # give Chrome a moment to open the port
        return self.chrome.is_available()

    def launch_chrome(self):
        """Called from the UI (Launch Chrome button)."""
        self._launch_attempted = False
        self._last_chrome_launch = 0.0
        try:
            message = self.chrome.launch()
            self.bridge.log.emit(message)
            return True
        except ChromeError as exc:
            self.bridge.log.emit(f"Chrome launch failed: {exc}")
            return False

    def launch_pwa(self, url):
        """Called from the UI (Launch as PWA / PWA URL launcher)."""
        try:
            message = self.chrome.launch_pwa(url)
            self.bridge.log.emit(message)
            return True
        except ChromeError as exc:
            self.bridge.log.emit(f"PWA launch failed: {exc}")
            return False

    # ------------------------------------------------------------ overrides
    @staticmethod
    def _override_key(url):
        """Stable key for a page: scheme + host + path (no query/fragment)."""
        parts = urlsplit(url)
        return f"{parts.scheme}://{parts.netloc}{parts.path}"

    def _apply_manual_override(self, detection):
        overrides = self.cfg.data.overrides or {}
        override = overrides.get(self._override_key(detection.url))
        if not override:
            return
        if override.get("title"):
            detection.cleaned_title = override["title"]
            detection.raw_title = override["title"]
        if override.get("episode") not in (None, ""):
            if str(override["episode"]) != str(detection.episode):
                detection.episode_title = ""
            detection.episode = override["episode"]

    def set_manual_override(self, url, title, episode=None):
        """Persist a user-supplied title/episode for a page (the "Wrong match?"
        button). Empty title clears any saved override."""
        overrides = dict(self.cfg.data.overrides or {})
        key = self._override_key(url)
        if not title:
            overrides.pop(key, None)
        else:
            overrides[key] = {"title": title, "episode": episode}
        self.cfg.set(overrides=overrides)
        self._last_recent_key = None  # let the next tick record with new identity

    # ------------------------------------------------------------- gathering
    def _gather_pages(self, pages, iframes):
        children = {}
        for frame in iframes:
            children.setdefault(frame.parent_id, []).append(frame)

        candidates = []
        for page in pages:
            ok, info = self.chrome.evaluate(page, PAGE_INFO_JS)
            if not ok or not isinstance(info, dict):
                info = {}
            video_raw = info.get("video") or {}
            has_video = bool(video_raw.get("hasVideo"))

            # The <video> often lives in a cross-origin iframe (player host).
            # Its CDP target has this page's id as parentId, so attach there.
            if not has_video:
                for frame in children.get(page.id, []):
                    ok2, vinfo = self.chrome.evaluate(frame, PAGE_INFO_JS)
                    if ok2 and isinstance(vinfo, dict) and vinfo.get("video", {}).get("hasVideo"):
                        video_raw = vinfo.get("video")
                        info.setdefault("video", {}).update(video_raw)
                        info["video_iframe_url"] = frame.url
                        has_video = True
                        break

            candidates.append(Candidate(page, info, _to_video_state(video_raw)))
        return candidates

    @staticmethod
    def _score(candidate):
        """Rank tabs: prefer visible, focused, playing, loaded videos."""
        video, info = candidate.video, candidate.info
        score = 10.0 if video.has_video else 0.0
        if not video.paused:
            score += 5.0
        if video.ended:
            score -= 6.0
        if video.ready_state >= 3:
            score += 2.0
        if info.get("visibility") == "visible":
            score += 4.0
        if info.get("focused"):
            score += 3.0
        return score

    def _choose_best(self, candidates):
        if not candidates:
            return None
        scored = [(self._score(c), c) for c in candidates]
        best_score = max(s for s, _ in scored)
        top = [c for s, c in scored if s == best_score]
        # Sticky tab: on a tie, keep the site currently being watched so two
        # open anime tabs don't make the presence flap between them.
        if len(top) > 1 and self._current is not None:
            current_host = urlsplit(self._current.detection.url).netloc.lower()
            for c in top:
                if urlsplit(c.target.url).netloc.lower() == current_host:
                    return c if c.video.has_video else None
        best = top[0]
        return best if best.video.has_video else None

    def _build_context(self, candidate):
        """Build a PageContext, pulling __NEXT_DATA__ for Crunchyroll on demand."""
        url = candidate.target.url
        next_data = None
        host = urlsplit(url).netloc.lower()
        # __NEXT_DATA__ can only be read through CDP; an extension-reporting
        # normal browser falls back to the Crunchyroll page-title pattern.
        if "crunchyroll" in host and self.chrome.is_available():
            cached = self._next_data_cache.get(candidate.target.id)
            if cached and cached[0] == url:
                next_data = cached[1]
            else:
                ok, raw = self.chrome.evaluate(candidate.target, NEXT_DATA_JS)
                next_data = raw if ok and isinstance(raw, str) else None
                self._next_data_cache[candidate.target.id] = (url, next_data)
        return PageContext(
            url=url,
            document_title=candidate.info.get("title") or candidate.target.title,
            next_data=next_data,
            episode=candidate.info.get("episode"),
            episode_title=candidate.info.get("episodeTitle") or "",
        )

    # -------------------------------------------------------------- no video
    def _no_video(self):
        """No tab is playing a video: flush watch time, hide RPC (per config),
        or send an idle presence when enabled."""
        if self._current is not None:
            self._flush_watch(self._current.key)
            self._current = None
            if not self.cfg.data.idle_presence and self.cfg.data.hide_when_no_video:
                self._clear_rpc()
            self.bridge.detection.emit({"clear": True})
        elif self.cfg.data.idle_presence:
            self._send_idle_presence()

    def _no_anime(self):
        self._no_video()  # same behavior: nothing to show

    # ------------------------------------------------- manual / idle / watch
    def set_manual_presence(self, title, episode="", website="", url=""):
        """User-driven "Watch anything" presence. Empty title clears it."""
        if title:
            self._manual = {
                "title": title,
                "episode": episode or None,
                "website": website or "",
                "url": url or "",
            }
        else:
            self._manual = None
        self._last_sent = None  # force the next tick to broadcast

    def clear_manual_presence(self):
        """Public entry point for the UI 'Clear manual' button."""
        self.set_manual_presence("")

    def _sync_manual(self):
        manual = self._manual or {}
        if not self.cfg.data.rpc_enabled:
            return
        title = manual.get("title") or "Anime"
        episode = manual.get("episode") or ""
        ep = f"Episode {episode}" if episode else ""
        details = f"Watching: {title}"[:128]
        state = (ep or "Watching")[:128]
        buttons = []
        if manual.get("url"):
            buttons.append({"label": "Watch Anime", "url": manual["url"]})
        payload = {"details": details, "state": state, "buttons": buttons, "large_image": None}
        if not self._should_send(payload):
            return
        broadcast = self.cfg.data.rpc_broadcast
        if broadcast in ("discord", "both"):
            try:
                self._send_rpc(details, state, None, title, None, None, buttons)
                self._set_discord_status(True, "Connected")
            except Exception as exc:  # noqa: BLE001
                self._set_discord_status(False, str(exc))
        if broadcast in ("vencord", "both"):
            self._send_vencord(payload, details)
        self._last_sent = {"sig": self._signature(payload), "end": None, "at": time.time()}

    def _send_idle_presence(self):
        if not self.cfg.data.rpc_enabled:
            return
        details = "Browsing anime"
        state = "Nothing playing"
        buttons = []
        payload = {"details": details, "state": state, "buttons": buttons, "large_image": None}
        if not self._should_send(payload):
            return
        broadcast = self.cfg.data.rpc_broadcast
        if broadcast in ("discord", "both"):
            try:
                self._send_rpc(details, state, None, details, None, None, buttons)
                self._set_discord_status(True, "Connected")
            except Exception as exc:  # noqa: BLE001
                self._set_discord_status(False, str(exc))
        if broadcast in ("vencord", "both"):
            self._send_vencord(payload, details)
        self._last_sent = {"sig": self._signature(payload), "end": None, "at": time.time()}

    def _accumulate_watch_time(self):
        """Accumulate real playback seconds (per-session delta from the video
        timer) and flush them into RecentHistory for the stats dialog."""
        current = self._current
        if current is None:
            return
        video = current.detection.video or VideoState()
        now = video.current_time if video.has_video else 0.0
        prev = self._watch_progress.get(current.key)
        if prev is not None:
            delta = now - prev
            if 0 <= delta <= 20:  # guard against seeks and jumps
                self._watch_accum[current.key] = self._watch_accum.get(current.key, 0) + delta
                self._flush_watch(current.key)
        self._watch_progress[current.key] = now

    @staticmethod
    def _episode_complete(video):
        """Sync only when a finite video has reached at least 90% playback."""
        import math
        if (video is None or not video.has_video or video.duration <= 0
                or not math.isfinite(video.duration) or not math.isfinite(video.current_time)):
            return False
        return video.current_time >= video.duration * 0.9

    def _maybe_sync_anilist(self, session, video):
        """Advance AniList once per episode after playback is confidently complete."""
        if not self.cfg.data.anilist_sync_enabled or not self.cfg.data.anilist_access_token:
            return
        if self._manual or not self.cfg.data.detection_enabled:
            return
        if session.media is None or not self._episode_complete(video):
            return
        try:
            episode = int(session.detection.episode)
        except (TypeError, ValueError):
            return
        if episode <= 0:
            return
        import hashlib
        token = self.cfg.data.anilist_access_token.strip()
        sync_key = (hashlib.sha256(token.encode()).hexdigest(), session.media.id, episode)
        with self._anilist_status_lock:
            if sync_key in self._anilist_synced or sync_key in self._anilist_sync_inflight:
                return
            if time.monotonic() < self._anilist_sync_retry.get(sync_key, 0):
                return
            self._anilist_sync_inflight.add(sync_key)
            self._anilist_pending[sync_key] = session
        self._emit_sync_status(activity=f"Updating episode {episode}…")
        media_id = session.media.id
        total = session.media.episodes

        def _run():
            try:
                # Serialize read/update pairs and retain the token captured for
                # this episode even if Settings changes while work is queued.
                with self._anilist_sync_lock:
                    if (not self.cfg.data.anilist_sync_enabled or self.stop_event.is_set()
                            or token != self.cfg.data.anilist_access_token.strip()):
                        return
                    client = AniListClient(access_token=token)
                    try:
                        updated = client.sync_progress(media_id, episode, total)
                    finally:
                        client._session.close()
                if updated:
                    self.bridge.log.emit(
                        f"AniList progress updated: {session.media.display_title()} "
                        f"episode {episode}."
                    )
                with self._anilist_status_lock:
                    self._anilist_synced.add(sync_key)
                    self._anilist_pending.pop(sync_key, None)
                    self._anilist_pending_errors.pop(sync_key, None)
                    self._anilist_sync_retry.pop(sync_key, None)
                if token == self.cfg.data.anilist_access_token.strip():
                    self.cfg.set(anilist_last_sync=time.time())
                    self._emit_sync_status(error="", activity=f"Episode {episode} is up to date")
            except Exception as exc:  # noqa: BLE001 - syncing must never stop detection
                with self._anilist_status_lock:
                    self._anilist_sync_retry[sync_key] = time.monotonic() + 60
                    self._anilist_pending_errors[sync_key] = str(exc)
                if token == self.cfg.data.anilist_access_token.strip():
                    self._emit_sync_status(error=str(exc), activity=f"Episode {episode} needs retry")
                self.bridge.log.emit(f"AniList sync failed for episode {episode}: {exc}")
            finally:
                with self._anilist_status_lock:
                    self._anilist_sync_inflight.discard(sync_key)
                    if sync_key in self._anilist_pending:
                        self._anilist_sync_retry.setdefault(sync_key, time.monotonic() + 60)
                self._emit_sync_status()

        threading.Thread(target=_run, daemon=True).start()

    def sync_status(self):
        import hashlib
        token_key = hashlib.sha256(self.cfg.data.anilist_access_token.strip().encode()).hexdigest()
        with self._anilist_status_lock:
            pending = [f"{session.media.display_title()} · Episode {key[2]}"
                       for key, session in self._anilist_pending.items() if key[0] == token_key]
            errors = list(dict.fromkeys(error for key, error in self._anilist_pending_errors.items()
                                        if key[0] == token_key))
            return {'account': self.cfg.data.anilist_sync_account,
                    'last_success': self.cfg.data.anilist_last_sync,
                    'pending': pending, 'error': '; '.join(errors) or self._anilist_sync_error,
                    'activity': self._anilist_sync_activity}

    def _emit_sync_status(self, error=None, activity=None):
        with self._anilist_status_lock:
            if error is not None:
                self._anilist_sync_error = error
            if activity is not None:
                self._anilist_sync_activity = activity
            status = self.sync_status()
        self.bridge.anilist_sync_status.emit(status)

    def retry_anilist_sync(self):
        """Retry failed completed episodes, even after the viewer changes tabs."""
        self._retry_pending_episodes(force=True)
        self.sync_anilist()

    def _retry_pending_episodes(self, force=False):
        import hashlib
        if (not self.cfg.data.anilist_sync_enabled or not self.cfg.data.detection_enabled
                or self._manual or self.stop_event.is_set()):
            return
        token_key = hashlib.sha256(self.cfg.data.anilist_access_token.strip().encode()).hexdigest()
        with self._anilist_status_lock:
            jobs = [(key, session) for key, session in self._anilist_pending.items()
                    if key[0] == token_key and key not in self._anilist_sync_inflight
                    and (force or time.monotonic() >= self._anilist_sync_retry.get(key, float('inf')))]
            for key, _ in jobs:
                self._anilist_sync_retry.pop(key, None)
        for _, session in jobs:
            self._maybe_sync_anilist(session, session.detection.video)

    def _flush_watch(self, key):
        secs = self._watch_accum.pop(key, 0)
        if secs <= 0:
            return
        meta = self._watch_meta.get(key)
        if not meta:
            return
        title, episode, website, url, cover = meta
        playback = None
        if self._current and self._current.key == key:
            video = self._current.detection.video
            if video:
                playback = {"current_time": video.current_time, "duration": video.duration}
        self.recent.add_watch_seconds(title, episode, website, secs, playback=playback)
        media = self._current.media if self._current and self._current.key == key else None
        self.watchlog.add(media.display_title() if media else title, episode, website, url, cover, secs,
                          media_id=media.id if media else None)
        # Throttle UI refreshes — the watch total changes every poll, but the
        # recent list only needs to redraw (and re-resolve icons) occasionally.
        if time.time() - self._last_recent_emit >= 10:
            self._last_recent_emit = time.time()
            self.bridge.recent.emit(self.recent.items())

    # ------------------------------------------------------------- anilist
    def _resolve_anime(self, detection):
        # Fast path: some sites (Miruro) embed the AniList ID directly in the URL.
        if detection.anilist_id:
            id_key = f"id:{detection.anilist_id}"
            cached = self.cache.get(id_key)
            if cached:
                return Media.from_dict(cached), 100.0
            try:
                media = self.anilist.get_media(detection.anilist_id)
            except AniListError as exc:
                self.bridge.log.emit(f"AniList error: {exc}")
                media = None
            if media:
                self.cache.set(id_key, self.anilist.as_dict(media))
                return media, 100.0
            if not (detection.cleaned_title or detection.raw_title):
                return None, 0.0

        search = detection.cleaned_title or detection.raw_title
        if not search:
            return None, 0.0

        key = normalize_search(search)
        cached = self.cache.get(key)
        if cached:
            media = Media.from_dict(cached)
            return media, 100.0

        # Same unresolvable query keeps flowing in every poll tick (e.g. a
        # page whose title can't be matched). AniList rate-limits hard; bang
        # their API once and don't retry the identical key for a few minutes.
        now = time.time()
        if key in self._anilist_miss and now - self._anilist_miss[key] < MISS_TTL:
            return None, 0.0

        try:
            results = []
            # AniList search is strict; walk progressively shorter terms and
            # stop at the first term that returns candidates.
            for term in search_terms(search):
                results = self.anilist.search_media(term, per_page=5)
                if results:
                    break
        except AniListError as exc:
            self.bridge.log.emit(f"AniList error: {exc}")
            results = []

        media, confidence = best_match(results, search, self.cfg.data.min_match_score)
        if media:
            self.cache.set(key, self.anilist.as_dict(media))
            if confidence < self.cfg.data.min_match_score:
                self.bridge.log.emit(
                    f"Low-confidence AniList match ({confidence:.0f}%) for '{search}' -> "
                    f"'{media.display_title()}'"
                )
                self._prompt_match(detection, results, search, media, confidence)
        else:
            self._anilist_miss[key] = now
            self.bridge.log.emit(f"No AniList match found for '{search}'")
            if results:
                self._prompt_match(detection, results, search, None, 0.0)
        return media, confidence

    def _prompt_match(self, detection, results, search, current, confidence):
        """Offer the user an inline picker when the automatic match is shaky.

        Runs from the worker thread; throttled to one prompt per URL per minute
        so a misdetected page doesn't spam the UI on every poll tick. Choosing
        a candidate calls ``set_manual_override`` which pins that title to the
        URL, so a wrong guess only needs fixing once.
        """
        now = time.time()
        if now - self._match_prompted.get(detection.url, 0.0) < 60.0:
            return
        self._match_prompted[detection.url] = now
        candidates = [
            {
                "id": m.id,
                "title": m.display_title(),
                "cover": m.cover_extra_large or m.cover_large,
                "url": m.site_url,
                "episodes": m.episodes,
                "is_current": m is current,
            }
            for m in results[:5]
        ]
        self.bridge.match_pending.emit(
            {
                "url": detection.url,
                "search": search,
                "current_title": (
                    current.display_title()
                    if current is not None
                    else (detection.cleaned_title or detection.raw_title)
                ),
                "confidence": round(confidence),
                "candidates": candidates,
            }
        )

    # -------------------------------------------------------------- session
    def _update_session(self, detection, media, confidence):
        identity = (
            detection.website,
            normalize_search(detection.cleaned_title or detection.raw_title),
            detection.episode,
        )
        key = "|".join(str(x) for x in identity)
        previous = self._current
        if previous and previous.key == key:
            session_start = previous.session_start
        else:
            if previous is not None:
                self._flush_watch(previous.key)
            session_start = time.time()
            logger.info("New session: %s", key)

        self._current = CurrentSession(detection, media, confidence, session_start, key)
        self._maybe_sync_anilist(self._current, detection.video)
        self._watch_meta[key] = (
            detection.cleaned_title or detection.raw_title,
            detection.episode,
            detection.website,
            detection.url,
            (media.cover_extra_large or media.cover_large) if media else "",
        )
        self._maybe_add_recent()
        self._sync_presence()
        self.bridge.detection.emit(self._ui_payload())

    def _maybe_add_recent(self):
        if self._current.key == self._last_recent_key:
            return
        self._last_recent_key = self._current.key
        detection, media = self._current.detection, self._current.media
        entry = {
            "title": (media.display_title() if media else None)
                     or detection.cleaned_title or detection.raw_title,
            "episode": detection.episode,
            "website": detection.website,
            "episode_title": detection.episode_title,
            "season_year": media.season_year if media else None,
            "aliases": [media.title_english, media.title_romaji, media.title_native] if media else [],
            "title_english": media.title_english if media else "",
            "url": detection.url,
            "anilist_id": media.id if media else None,
            "anilist_url": media.site_url if media else "",
            "cover": (media.cover_extra_large or media.cover_large) if media else "",
            "episodes_total": media.episodes if media else None,
            "banner_url": getattr(media, "banner_url", "") if media else "",
            "current_time": detection.video.current_time if detection.video else None,
            "duration": detection.video.duration if detection.video else None,
        }
        self.recent.add(entry)
        self.bridge.recent.emit(self.recent.items())

    def remove_recent_entry(self, entry):
        """Delete one history entry and refresh the UI list."""
        from .history import history_key
        from .media_identity import MediaIdentity
        local = self.recent.items()
        resolver = MediaIdentity([*local, *self.watchlog.history(local), entry])
        key = history_key(entry, resolver)
        for candidate in local:
            if history_key(candidate, resolver) == key:
                self.recent.remove(candidate)
        self.watchlog.dismiss_history(dict(entry, history_key=list(key)))
        self.bridge.recent.emit(self.recent.items())

    def remove_continue_entry(self, title):
        """Drop *title* from continue watching and the watch-time stats."""
        self.watchlog.remove_title(title)
        self.bridge.recent.emit(self.recent.items())

    def clear_recent(self):
        """Delete all history entries and refresh the UI list."""
        self.recent.clear()
        self.watchlog.clear_feed_history()
        self.bridge.recent.emit(self.recent.items())

    # ------------------------------------------------------------- anilist
    def _make_watchlog(self):
        """WatchLog for the active account (default shared file when none)."""
        return WatchLog(path=watchlog_path_for(self._active_account, APP_DIR))

    # ------------------------------------------------------------ anilist
    @property
    def anilist_accounts(self):
        return list(self._accounts)

    @property
    def active_anilist_account(self):
        return self._active_account

    def add_anilist_account(self, username):
        """Add an AniList account and make it active (returns the name)."""
        username = (username or "").strip()
        if not username:
            return ""
        if username not in self._accounts:
            self._accounts.append(username)
        self.switch_anilist_account(username)
        return username

    def switch_anilist_account(self, username):
        """Switch the active profile; empty username selects the shared default.

        Each account's watch history lives in its own save file. The first
        account switch migrates the old single-file ``watchlog.json`` into that
        account's file so existing data isn't stranded.
        """
        username = (username or "").strip()
        if username and username not in self._accounts:
            self._accounts.append(username)
        self._active_account = username
        self.cfg.set(anilist_username=username, anilist_accounts=self._accounts)
        if username:
            legacy = watchlog_path_for("", APP_DIR)
            target = watchlog_path_for(username, APP_DIR)
            if not target.exists() and legacy.exists():
                try:
                    legacy.replace(target)
                except OSError:
                    try:
                        import shutil
                        shutil.copy2(legacy, target)
                    except OSError:
                        pass
        self.watchlog = self._make_watchlog()
        label = f"AniList account {username}" if username else "default profile"
        self.bridge.log.emit(f"Switched to {label}.")

    def remove_anilist_account(self, username):
        """Remove *username* from the account list (its save file is kept)."""
        username = (username or "").strip()
        if not username or username not in self._accounts:
            return
        self._accounts.remove(username)
        self.cfg.set(anilist_accounts=self._accounts)
        if self._active_account == username:
            self.switch_anilist_account("")

    def fetch_account_avatar(self, username=None):
        """Resolve the account avatar URL off the UI thread and emit it.

        Emits ``bridge.account_avatar(username, url)``; the URL is "" when the
        profile has no avatar or the lookup failed. Used by the account
        indicator so you can see at a glance which profile is active.
        """
        username = username or self._active_account or ""
        if not username:
            self.bridge.account_avatar.emit("", "")
            return
        if self.cfg.data.anilist_cache_ttl_days:
            cached = self.cache.get(f"user:{username}:avatar")
            if cached:
                self.bridge.account_avatar.emit(username, cached)
                return

        def _run():
            url = ""
            try:
                url = self.anilist.fetch_user_profile(username) or ""
            except Exception as exc:  # noqa: BLE001 - indicator is best-effort
                self.bridge.log.emit(f"Avatar fetch failed for {username}: {exc}")
            if url:
                self.cache.set(f"user:{username}:avatar", url)
            self.bridge.account_avatar.emit(username, url)

        threading.Thread(target=_run, daemon=True).start()

    def sync_anilist(self, username=None):
        """Two-way AniList sync: pull the list here, push progress live.

        With an access token set, the *pull* reads the authenticated viewer's
        own list (works for private profiles and reflects edits made on
        AniList); without a token it falls back to the active account's public
        list. Imported progress is reconciled into the local watch log
        (idempotent: re-syncing replaces the previous import) and the Library
        tab is refreshed. The *push* half advances AniList progress at 90%
        playback and runs automatically as episodes complete (see
        :meth:`_maybe_sync_anilist`). Runs in a background thread (network I/O).
        """
        import threading

        token = (self.cfg.data.anilist_access_token or "").strip()
        account = (username or self._active_account
                   or (self.cfg.data.anilist_username or "").strip())
        if not token and not account:
            message = "No AniList account or token set."
            self.bridge.log.emit(f"AniList sync skipped: {message}")
            self.bridge.stats_imported.emit(0, message)
            self.bridge.anilist_synced.emit({"account": "", "titles": 0, "message": message})
            return
        with self._anilist_status_lock:
            if self._anilist_pull_inflight:
                if token != self._anilist_pull_token:
                    self._anilist_pull_again = True
                return
            self._anilist_pull_inflight = True
            self._anilist_pull_token = token
        self._emit_sync_status(activity="Reading AniList list…")
        # Snapshot the profile so a mid-flight account switch can't cross files.
        watchlog = self.watchlog

        def _pull():
            client = AniListClient(access_token=token)
            try:
                if token:
                    entries, viewer_name = client.fetch_viewer_list()
                else:
                    entries = client.fetch_user_list(account)
                    viewer_name = account
            except Exception as exc:  # noqa: BLE001 - report and stop
                message = f"AniList sync failed: {exc}"
                self.bridge.log.emit(message)
                self.bridge.stats_imported.emit(0, message)
                self.bridge.anilist_synced.emit(
                    {"account": account, "titles": 0, "message": message}
                )
                self._emit_sync_status(error=str(exc), activity="List sync failed")
                return
            finally:
                client._session.close()

            if token != self.cfg.data.anilist_access_token.strip() or self.stop_event.is_set():
                self.bridge.anilist_synced.emit({'message': 'Sync cancelled: account changed.'})
                return
            target_log = watchlog
            if token and viewer_name.casefold() != account.casefold():
                target_log = WatchLog(path=watchlog_path_for(viewer_name, APP_DIR))
            records = []
            sections = {"Watching": [], "Planning": [], "Completed": []}
            for item in entries:
                media = item.get("media")
                if media is None:
                    continue
                records, sections = self._categorize_list_entry(
                    item, media, records, sections
                )
            imported = target_log.import_titles(records)
            target_log.set_anilist_sections(sections)
            label = viewer_name or account or "AniList"
            count = sum(len(v) for v in sections.values())
            self.bridge.log.emit(
                f"AniList sync ({label}): seeded {len(records)} titles "
                f"({format_hours(imported)}), {count} list entries."
            )
            self.bridge.library_loaded.emit(
                {
                    "username": label,
                    "sections": self._build_library_sections(entries),
                    "message": "",
                }
            )
            self.bridge.stats_imported.emit(len(records), "")
            self.bridge.anilist_synced.emit(
                {"account": label, "titles": len(records), "message": ""}
            )
            if token:
                self.cfg.set(anilist_sync_account=viewer_name, anilist_last_sync=time.time())
            self._emit_sync_status(error="", activity=f"Imported {len(records)} titles from {label}")

        def _run():
            try:
                with self._anilist_sync_lock:
                    _pull()
            except Exception as exc:
                self._emit_sync_status(error=str(exc), activity="List sync failed")
                self.bridge.anilist_synced.emit({'message': f'Sync failed: {exc}'})
            finally:
                with self._anilist_status_lock:
                    self._anilist_pull_inflight = False
                    restart = self._anilist_pull_again
                    self._anilist_pull_again = False
                if restart and not self.stop_event.is_set():
                    self.sync_anilist()

        threading.Thread(target=_run, daemon=True).start()

    def import_anilist_history(self, username=None):
        """Back-compat alias for :meth:`sync_anilist` (stats/Library refresh)."""
        self.sync_anilist(username)

    def refresh_activity_stats(self, force=False):
        """Read this profile's activity off-thread, at most once per five minutes."""
        account, log = self._active_account, self.watchlog
        if not account:
            return
        key = account.casefold()
        with self._anilist_status_lock:
            if key in self._activity_inflight:
                return
            last = max(self._activity_attempts.get(key, 0), log.activity().get("fetched_at", 0))
            if not force and time.time()-last < 300:
                return
            self._activity_inflight.add(key)
            self._activity_attempts[key] = time.time()
        token = self.cfg.data.anilist_access_token if self.cfg.data.anilist_sync_account.casefold() == key else ""
        self.bridge.activity_stats.emit({"account": account, "loading": True})

        def _run():
            client = AniListClient(access_token=token)
            message = ""
            try:
                def cancelled():
                    return self.stop_event.is_set() or self.watchlog is not log
                def progress(snapshot, page):
                    if not cancelled() and not snapshot.get("complete") and (page == 1 or page % 5 == 0):
                        log.set_activity(snapshot)
                        log.history(self.recent.items())
                        log.analytics()
                        self.bridge.activity_stats.emit({"account": account, "loading": True,
                            "message": f"Loading AniList history · {len(snapshot['items']):,} activities fetched…", "changed": True})
                snapshot = client.fetch_user_activity(account, previous=log.activity(), on_page=progress, cancelled=cancelled)
                if self.stop_event.is_set() or self.watchlog is not log:
                    return
                log.set_activity(snapshot)
                log.history(self.recent.items())
                log.analytics()
            except Exception as exc:
                message = f"Activity refresh failed; keeping cached data: {exc}"
                self.bridge.log.emit(message)
            finally:
                client._session.close()
                with self._anilist_status_lock:
                    self._activity_inflight.discard(key)
                self.bridge.activity_stats.emit({"account": account, "loading": False, "message": message})

        threading.Thread(target=_run, daemon=True).start()

    def refresh_library(self, username=None):
        """Fetch the active account's full AniList list for the Library tab.

        Runs in a background thread (network I/O). Emits ``library_loaded``
        with ``{"username", "sections", "message"}``; sections group the list
        membership by status so the UI can render Watching/Planning/Completed/
        Paused/Dropped.
        """
        import threading

        username = username or self._active_account or (self.cfg.data.anilist_username or "").strip()
        if not username:
            self.bridge.library_loaded.emit(
                {"username": "", "sections": {}, "message": "No AniList username set."}
            )
            return

        token = self.cfg.data.anilist_access_token if self.cfg.data.anilist_sync_account.casefold() == username.casefold() else ""
        def _run():
            client = AniListClient(access_token=token)
            try:
                with self._anilist_sync_lock:
                    entries = client.fetch_user_list(username)
            except Exception as exc:  # noqa: BLE001 - report and stop
                self.bridge.log.emit(f"Library fetch failed: {exc}")
                self.bridge.library_loaded.emit(
                    {"username": username, "sections": {}, "message": f"Fetch failed: {exc}"}
                )
                return
            finally:
                client._session.close()
            sections = self._build_library_sections(entries)
            self.bridge.library_loaded.emit({"username": username, "sections": sections, "message": ""})

        threading.Thread(target=_run, daemon=True).start()

    def change_library_status(self, entry, status):
        from .media_identity import media_id
        identity = media_id(entry)
        account = self._active_account
        token = self.cfg.data.anilist_access_token.strip()
        log = self.watchlog
        payload = {"account": account, "media_id": identity, "status": status, "entry": dict(entry)}
        if not token or not account or not identity:
            self.bridge.library_changed.emit(dict(payload, error="Connect this AniList account in Settings to edit its library."))
            return
        with self._anilist_status_lock:
            pending = getattr(self, "_library_edits", set())
            self._library_edits = pending
            key = (account.casefold(), identity)
            if key in pending:
                return
            pending.add(key)
        self.bridge.library_changed.emit(dict(payload, loading=True))
        def run():
            client = AniListClient(access_token=token)
            result = {}
            try:
                with self._anilist_sync_lock:
                    if token != self.cfg.data.anilist_access_token.strip() or self.stop_event.is_set():
                        raise AniListError("Library change cancelled: account connection changed.")
                    result["result"] = client.change_list_status(identity, status, account)
                    sections = log.anilist_sections()
                    saved = next((e for rows in sections.values() for e in rows if media_id(e) == identity), entry)
                    sections = {label: [e for e in rows if media_id(e) != identity] for label, rows in sections.items()}
                    if status != "REMOVE":
                        label = {"CURRENT": "Watching", "REPEATING": "Watching", "PLANNING": "Planning",
                                 "COMPLETED": "Completed", "PAUSED": "Paused", "DROPPED": "Dropped"}[status]
                        sections.setdefault(label, []).append(dict(saved, status=status, progress=result["result"].get("progress", saved.get("progress", 0))))
                    log.set_anilist_sections(sections)
            except Exception as exc:
                result["error"] = str(exc)
            finally:
                client._session.close()
                with self._anilist_status_lock:
                    self._library_edits.discard(key)
                self.bridge.library_changed.emit(dict(payload, loading=False, **result))
        threading.Thread(target=run, daemon=True).start()

    @staticmethod
    def _current_season(when=None):
        """Return (season_name, year) for a date, AniList season-style."""
        from datetime import date

        when = when or date.today()
        month = when.month
        if month in (1, 2, 3):
            season = "WINTER"
        elif month in (4, 5, 6):
            season = "SPRING"
        elif month in (7, 8, 9):
            season = "SUMMER"
        else:
            season = "FALL"
        return season, when.year

    def refresh_calendar(self, username=None):
        """Fetch the current season's catalogue + the user's library markers.

        Runs in a background thread. Emits ``calendar_loaded`` with
        ``{"username", "season", "entries", "message"}``; each entry carries
        ``in_library`` / ``library_status`` so the UI can mark titles the user
        already follows.
        """
        import threading

        username = username or self._active_account or (self.cfg.data.anilist_username or "").strip()
        season, year = self._current_season()

        def _run():
            try:
                seasonal = self.anilist.fetch_seasonal(season, year)
                library = self.anilist.fetch_user_list(username) if username else []
            except Exception as exc:  # noqa: BLE001 - report and stop
                self.bridge.log.emit(f"Calendar fetch failed: {exc}")
                self.bridge.calendar_loaded.emit(
                    {"username": username, "season": f"{season} {year}", "entries": [], "message": f"Fetch failed: {exc}"}
                )
                return
            entries = self._build_calendar_entries(seasonal, library)
            self.bridge.calendar_loaded.emit(
                {"username": username, "season": f"{season} {year}", "entries": entries, "message": ""}
            )

        threading.Thread(target=_run, daemon=True).start()

    @staticmethod
    def _build_calendar_entries(seasonal, library):
        """Build flat calendar entries, marking which the user already follows."""
        status_map = {}
        for item in library or []:
            media = item.get("media")
            if media is not None:
                status_map[media.id] = (item.get("status") or "").upper()
        entries = []
        for media in seasonal or []:
            status = status_map.get(media.id)
            entries.append(
                {
                    "title": media.display_title(),
                    "url": media.site_url,
                    "aliases": [media.title_english, media.title_romaji, media.title_native],
                    "title_english": media.title_english,
                    "cover": media.cover_extra_large or media.cover_large,
                    "anilist_id": media.id,
                    "episodes": media.episodes,
                    "mean_score": media.mean_score,
                    "genres": list(media.genres),
                    "season": media.season,
                    "season_year": media.season_year,
                    "next_airing_at": media.next_airing_at,
                    "next_airing_episode": media.next_airing_episode,
                    "in_library": status is not None,
                    "library_status": status or "",
                    "keywords": " ".join(
                        [media.display_title()]
                        + list(media.genres)
                        + ([str(media.mean_score)] if media.mean_score else [])
                        + ([media.season] if media.season else [])
                    ).lower(),
                }
            )
        # Soonest-to-air first; unannounced (None) entries trail behind.
        entries.sort(key=lambda e: (e["next_airing_at"] is None, e["next_airing_at"] or 0))
        return entries

    @staticmethod
    def _build_library_sections(entries):
        """Group fetched list entries into the five Library sections."""
        sections = {"Watching": [], "Planning": [], "Completed": [], "Paused": [], "Dropped": []}
        for item in entries or []:
            media = item.get("media")
            if media is None:
                continue
            status = (item.get("status") or "").upper()
            progress = max(int(item.get("progress") or 0), 0)
            entry = {
                "title": media.display_title(),
                "aliases": [media.title_english, media.title_romaji, media.title_native],
                "title_english": media.title_english,
                "created_at": item.get("created_at") or 0,
                "updated_at": item.get("updated_at") or 0,
                "season_year": media.season_year,
                "url": media.site_url,
                "cover": media.cover_extra_large or media.cover_large,
                "anilist_id": media.id,
                "status": status,
                "progress": progress,
                "episodes": media.episodes,
                "mean_score": media.mean_score,
                "genres": list(media.genres),
                "next_airing_at": media.next_airing_at,
                "next_airing_episode": media.next_airing_episode,
                "keywords": " ".join(
                    [media.display_title()]
                    + list(media.genres)
                    + ([str(media.mean_score)] if media.mean_score else [])
                ).lower(),
            }
            if status in ("CURRENT", "REPEATING", "WATCHING"):
                sections["Watching"].append(entry)
            elif status == "PLANNING":
                sections["Planning"].append(entry)
            elif status == "COMPLETED":
                sections["Completed"].append(entry)
            elif status == "PAUSED":
                sections["Paused"].append(entry)
            elif status == "DROPPED":
                sections["Dropped"].append(entry)
        return sections

    @staticmethod
    def _categorize_list_entry(item, media, records, sections):
        """Split one fetched list entry into (records, sections) by its status."""
        status = (item.get("status") or "").upper()
        progress = max(int(item.get("progress") or 0), 0)
        section_entry = {
            "title": media.display_title(),
            "aliases": [media.title_english, media.title_romaji, media.title_native],
            "title_english": media.title_english,
            "created_at": item.get("created_at") or 0,
            "updated_at": item.get("updated_at") or 0,
            "season_year": media.season_year,
            "url": media.site_url,
            "cover": media.cover_extra_large or media.cover_large,
            "status": status,
            "progress": progress,
            "episodes": media.episodes,
            "seconds": progress * (media.duration or 24) * 60,
            "media_id": media.id,
            "genres": list(media.genres),
            "mean_score": media.mean_score,
        }
        if status in ("CURRENT", "REPEATING", "WATCHING"):
            sections["Watching"].append(section_entry)
        elif status == "PLANNING":
            sections["Planning"].append(section_entry)
        elif status == "COMPLETED":
            sections["Completed"].append(section_entry)
        if status in ("CURRENT", "COMPLETED", "REPEATING", "PAUSED", "DROPPED") and progress > 0:
            records.append(
                {
                    "title": media.display_title(),
                    "episode": progress,
                    "aliases": [media.title_english, media.title_romaji, media.title_native],
                    "title_english": media.title_english,
                    "season_year": media.season_year,
                    "website": "AniList",
                    "url": media.site_url,
                    "cover": media.cover_extra_large or media.cover_large,
                    "seconds": progress * (media.duration or 24) * 60,
                    "media_id": media.id,
                }
            )
        return records, sections

    def _ui_payload(self):
        detection = self._current.detection
        media = self._current.media
        video = detection.video or VideoState()
        title = (media.display_title() if media else None) or detection.cleaned_title or detection.raw_title
        return {
            "title": title,
            "detected_title": detection.cleaned_title or detection.raw_title,
            "anilist_id": media.id if media else None,
            "episode_title": detection.episode_title,
            "aliases": [media.title_english, media.title_romaji, media.title_native] if media else [],
            "title_english": media.title_english if media else "",
            "banner_url": getattr(media, "banner_url", "") if media else "",
            "episode": detection.episode,
            "episodes_total": media.episodes if media else None,
            "season": media.season if media else "",
            "season_year": media.season_year if media else None,
            "next_airing_at": media.next_airing_at if media else None,
            "next_airing_episode": media.next_airing_episode if media else None,
            "website": detection.website,
            "url": detection.url,
            "current_time": video.current_time,
            "duration": video.duration,
            "paused": video.paused,
            "cover_url": media.cover_extra_large or media.cover_large if media else "",
            "cover_extra_url": media.cover_extra_large if media else "",
            "anilist_url": media.site_url if media else "",
            "anilist_title": title,
            "match_confidence": self._current.confidence,
            "session_start": self._current.session_start,
        }

    def _preferred_cover(self, media):
        if not media:
            return None
        entry = {"title": media.display_title(), "media_id": media.id,
                 "title_english": media.title_english,
                  "aliases": [media.title_english, media.title_romaji, media.title_native],
                  "season_year": media.season_year, "episodes": media.episodes,
                  "cover": media.cover_extra_large or media.cover_large}
        return self.artwork.enrich(entry, priority=0).get("cover") or None

    # -------------------------------------------------------------- discord
    def _connect_discord(self):
        app_id = self.cfg.data.discord_application_id.strip()
        if not app_id:
            raise ValueError(
                "No Discord Application ID configured - set it in the app "
                "(Controls) or in config.json"
            )
        if self.discord.is_connected and not self._force_reconnect:
            return
        if self.discord.is_connected:
            self.discord.close()
        self.discord.connect(app_id)
        self._force_reconnect = False
        self._discord_warned = False
        self._set_discord_status(True, "Connected")
        self.bridge.log.emit("Connected to Discord Rich Presence")

    def _sync_presence(self):
        session = self._current
        if session is None:
            return
        detection, media = session.detection, session.media
        video = detection.video or VideoState()

        if video.paused and not self.cfg.data.show_paused_presence:
            self._clear_rpc()
            return
        if not self.cfg.data.rpc_enabled:
            return

        if self.cfg.data.hide_adult_content and media is not None and media.is_adult_content():
            # 18+/ecchi content never appears on the Discord status when the
            # setting is on; the local dashboard still records and shows it.
            self._clear_rpc()
            self._set_discord_status(False, "Adult content hidden")
            return

        if self.cfg.data.rpc_broadcast in ("discord", "both") and not self.cfg.data.discord_application_id:
            # No App ID yet -> nothing to push down the pipe. A clear hint (once)
            # beats a repeated error; the UI reconnects automatically once the ID
            # is set. A vencord-only broadcast doesn't need it.
            self._set_discord_status(False, "No Application ID set")
            return

        title = (media.display_title() if media else None) or detection.cleaned_title or detection.raw_title
        episode = format_episode_progress(detection.episode, media.episodes if media else None)
        if self.cfg.data.presence_episode_names and detection.episode_title:
            episode = " · ".join(part for part in (episode, detection.episode_title) if part)

        airing = ""
        if self.cfg.data.presence_airing_dates and media and media.next_airing_at and not video.paused:
            airing = next_airing_label(
                media.next_airing_at, media.next_airing_episode, detection.episode
            )

        details = title[:128]
        if video.paused and self.cfg.data.presence_paused_label:
            state = ("Paused · " + episode) if episode else "Paused"
        else:
            # Discord's native progress bar already shows the playback time.
            state = " · ".join(part for part in (episode, airing) if part) or "Watching"
        state = state[:128]

        # Prefer the biggest cover AniList offers; the URL must carry a file
        # extension and be publicly reachable for Discord to proxy it. Never
        # send an empty value (Discord's Joi rejects "" on asset keys).
        cover = self._preferred_cover(media)
        if not self.cfg.data.presence_cover_art:
            cover = None
        if not cover:
            cover = None

        now_ts = int(time.time())
        # Discord advances timestamps against the wall clock and has no pause
        # flag. Omit them while paused; rebuild from the playhead on resume.
        start = None if video.paused else int(session.session_start)
        end = None
        if not video.paused and video.duration > 0:
            # Anchor both timestamps to the video's own timeline (position-based):
            # start = when the video would have started to sit at the current
            # position. Seeking forward or backward then never shrinks/stretches
            # the episode - Discord always renders it at its real length. A
            # fixed session_start alone would make end - start shrink by every
            # skipped second (e.g. a 24-min episode shows as 12 min after a
            # jump to the halfway mark).
            position = max(0.0, video.current_time)
            start = now_ts - int(position)
            end = now_ts + int(max(0.0, video.duration - position))

        buttons = [{"label": "Watch Anime", "url": detection.url}]
        if media and media.site_url:
            buttons.append({"label": "Open AniList", "url": media.site_url})

        payload = {
            "details": details,
            "state": state,
            "large_image": cover,
            "large_text": title[:128],
            "start": start,
            "end": end,
            "buttons": buttons,
        }

        if not self._should_send(payload):
            return
        self._update_rpc(details, state, cover, title, start, end, buttons, payload)
        self._last_sent = {"sig": self._signature(payload), "end": end, "at": time.time()}

    def now_playing(self):
        """Snapshot of the current presence for the public share page.

        Read by the localhost bridge's ``/now.json`` handler from a different
        thread, so it only reads immutable state and never mutates anything.
        Privacy mode blanks the page entirely (idle); hiding adult content
        keeps playback visible but withholds the title/cover.
        """
        base = {
            "app": "AniPresence",
            "version": __version__,
            "active": False,
            "private": bool(self.cfg.data.privacy_mode),
            "updated": int(time.time()),
        }
        if self.cfg.data.privacy_mode or self._manual:
            return base
        session = self._current
        if session is None:
            return base
        detection, media = session.detection, session.media
        video = detection.video or VideoState()
        title = (
            (media.display_title() if media else None)
            or detection.cleaned_title
            or detection.raw_title
        )
        cover = self._preferred_cover(media)
        hidden = (
            bool(self.cfg.data.hide_adult_content)
            and media is not None
            and media.is_adult_content()
        )
        base.update(
            {
                "active": True,
                "title": title or "",
                "url": detection.url or "",
                "site": detection.website or "",
                "episode": detection.episode or None,
                "episodes": media.episodes if media else None,
                "cover": cover or "",
                "status": "paused" if video.paused else "playing",
                "position": max(0.0, video.current_time),
                "duration": video.duration or 0,
                "media_url": media.site_url if media else "",
                "hidden": hidden,
            }
        )
        if hidden:
            base["title"] = ""
            base["cover"] = ""
            base["url"] = ""
            base["media_url"] = ""
        return base

    def _update_rpc(self, details, state, cover, title, start, end, buttons, payload):
        """Push a presence change through every configured broadcast channel.

        ``rpc_broadcast`` picks the target(s): the official Discord pipe, the
        local Vencord plugin bridge, or both. A failure on one channel never
        blocks the other.
        """
        broadcast = self.cfg.data.rpc_broadcast
        if broadcast in ("discord", "both"):
            try:
                self._send_rpc(details, state, cover, title, start, end, buttons)
                self._set_discord_status(True, "Connected")
            except Exception as exc:  # noqa: BLE001
                logger.debug("presence update failed: %s", exc)
                self._set_discord_status(False, str(exc))
        if broadcast in ("vencord", "both"):
            if not self._send_vencord(payload, details):
                self._set_discord_status(False, "Vencord bridge unreachable")

    def _send_vencord(self, payload, details):
        """Push an activity through the local Vencord plugin bridge.

        The plugin dispatches ``LOCAL_ACTIVITY_UPDATE`` on its side (the client
        mod accepts it regardless of Discord's app verification), which is what
        makes the presence visible to other users. Returns True when the plugin
        acknowledged the POST.
        """
        now = int(time.time())
        # An explicit None clears paused playback timestamps. Only activities
        # without a start field (manual/idle) get a default elapsed timer.
        start = payload.get("start", payload.get("timestamps", {}).get("start", now))
        end = payload.get("end") or payload.get("timestamps", {}).get("end")
        timestamps = {k: v for k, v in {"start": start, "end": end}.items() if v is not None}
        vencord_activity = {
            "application_id": self.cfg.data.discord_application_id,
            "name": "Anime",
            "type": 3,  # ActivityType.WATCHING
            "details": details[:128],
            "state": payload.get("state") or "",
            "timestamps": timestamps or None,
            "assets": {
                "large_image": payload.get("large_image"),
                "large_text": payload.get("large_text"),
            },
            "buttons": payload.get("buttons"),
            "metadata": {"button_urls": [b.get("url") for b in (payload.get("buttons") or [])]},
            "flags": 1,
            "status_display_type": 2,
        }
        activity = {k: v for k, v in vencord_activity.items() if v is not None}
        return self.vencord.send(activity, self._signature(payload))

    def _send_rpc(self, details, state, cover, title, start, end, buttons):
        """Push an activity through the official RPC pipe (owner-only unless the
        application is verified). WATCHING so the status reads "Watching ..."
        instead of the default "Playing ..."."""
        if not self.discord.is_connected:
            self._connect_discord()
        payload = {
            "details": details,
            "state": state,
            "large_image": cover,
            "large_text": title[:128],
            "start": start,
            "end": end,
            "buttons": buttons,
        }
        kwargs = {k: v for k, v in payload.items() if v is not None}
        self.discord.update(
            activity_type=ActivityType.WATCHING,
            status_display_type=StatusDisplayType.DETAILS,
            **kwargs,
        )

    @staticmethod
    def _signature(payload):
        stable = {k: payload[k] for k in ("details", "state", "buttons", "large_image")}
        stable['timed'] = payload.get('start') is not None or payload.get('end') is not None
        return json.dumps(stable, sort_keys=True, default=str)

    def _should_send(self, payload):
        """Throttle: send only on meaningful change or drift."""
        now = time.time()
        if self._last_sent is None:
            return True
        if self._signature(payload) != self._last_sent["sig"]:
            return True
        if (payload.get("end") is None) != (self._last_sent.get("end") is None):
            return True
        if payload.get("end") and self._last_sent.get("end"):
            if abs(payload["end"] - self._last_sent["end"]) >= 15:
                return True
        if now - self._last_sent["at"] > 20:
            return True
        return False

    def _clear_rpc(self):
        if self.cfg.data.rpc_broadcast in ("discord", "both"):
            try:
                self.discord.clear()
            except Exception:  # noqa: BLE001
                pass
        if self.cfg.data.rpc_broadcast in ("vencord", "both"):
            try:
                self.vencord.clear()
            except Exception:  # noqa: BLE001
                pass
        self._last_sent = None

    def clear_rpc(self):
        """Public entry point for the UI 'Clear RPC' button."""
        self._clear_rpc()

    def reconnect_discord(self):
        """Public entry point for the UI 'Reconnect Discord' button."""
        self._force_reconnect = True
        self._last_discord_attempt = 0.0  # connect on the very next tick
        try:
            self.discord.close()
        except Exception:  # noqa: BLE001
            pass
        self._set_discord_status(False, "Reconnecting...")

    # ------------------------------------------------------------ ui status
    def _set_chrome_status(self, connected, text):
        key = (connected, text)
        if key != self._last_chrome_state:
            self._last_chrome_state = key
            self.bridge.chrome_status.emit(text, connected)

    def _set_discord_status(self, connected, text):
        if text is None:
            text = "Connected" if connected else "Disconnected"
        key = (connected, text)
        if key != self._last_discord_state:
            self._last_discord_state = key
            self.bridge.discord_status.emit(text, connected)

    def request_stop(self):
        self.stop_event.set()
        self.artwork.close()


def _to_video_state(raw):
    """Convert the JS video dict into a VideoState dataclass."""
    if not raw:
        return VideoState()
    return VideoState(
        has_video=bool(raw.get("hasVideo")),
        current_time=float(raw.get("currentTime") or 0.0),
        duration=float(raw.get("duration") or 0.0),
        paused=bool(raw.get("paused", True)),
        ended=bool(raw.get("ended")),
        ready_state=int(raw.get("readyState") or 0),
        src=str(raw.get("src") or ""),
    )
