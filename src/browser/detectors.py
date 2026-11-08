"""Per-website anime detectors.

Each detector turns a tab's :class:`PageContext` (URL + document title + optional
structured data) into a normalized :class:`Detection` (title + episode + source).

To support a new website:
  1. Subclass :class:`BaseDetector` and register it in :class:`DetectorRegistry`,
     OR
  2. Add its host regex to ``KNOWN_SITES`` and let the generic extractor handle
     it with page-title + URL-slug heuristics.

Host matching is done on the lowercased hostname so ``www.`` prefixes and
mirror subdomains are handled by regex alone.
"""
import json
import logging
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from ..anime.titles import clean_slug, parse_episode, strip_trailing_season

logger = logging.getLogger("anipresence.detectors")


# --------------------------------------------------------------------------- #
# Shared data models
# --------------------------------------------------------------------------- #
@dataclass
class VideoState:
    """Snapshot of the HTML5 <video> element read via CDP."""
    has_video: bool = False
    current_time: float = 0.0
    duration: float = 0.0
    paused: bool = True
    ended: bool = False
    ready_state: int = 0
    src: str = ""


@dataclass
class Detection:
    """A successfully identified anime playing on a site."""
    website: str
    url: str
    raw_title: str = ""
    cleaned_title: str = ""
    episode: int = None
    source: str = "url"      # 'url' | 'page' | 'next_data' | 'slug'
    video: VideoState = None
    anilist_id: int = None   # known AniList ID (e.g. embedded in the URL)
    episode_title: str = ""  # name of the selected episode, when the site provides it


# Path segments that denote a site's home/browse/listing page rather than a
# specific anime being watched. These pages often autoplay a trailer <video>,
# which would otherwise make detection "find" an anime that isn't there and
# send an unresolvable title to AniList on every poll.
NAV_SEGMENTS = {
    "", "home", "index", "browse", "discover", "search", "explore",
    "watchlist", "favorites", "genre", "genres", "category", "categories",
    "trending", "popular", "top", "top-rated", "new", "newest", "recent",
    "latest", "random", "az-list", "azlist", "a-z", "schedule", "calendar",
    "movies", "series", "shows", "anime", "subbed", "dubbed", "help",
    "about", "faq", "contact", "login", "signup", "register",
}


def is_navigation_page(ctx_or_url):
    """True for a tab whose URL points at a site navigation page, not a video.

    ``ctx_or_url`` may be a :class:`PageContext` or a plain URL string. Only the
    first path segment is considered, so a real watch URL like
    ``/watch/1234/slug`` (whose first segment is ``watch``) is never refused.
    """
    url = ctx_or_url.url if hasattr(ctx_or_url, "url") else str(ctx_or_url or "")
    path = urlsplit(url).path
    segments = [s for s in path.split("/") if s]
    first = segments[0].lower() if segments else ""
    return first in NAV_SEGMENTS


@dataclass
class PageContext:
    """Everything a detector needs about a tab at one moment in time."""
    url: str
    document_title: str = ""
    next_data: str = None    # Crunchyroll __NEXT_DATA__ JSON string (when present)
    episode: int = None      # selected episode read from the player page's DOM
    episode_title: str = ""

    @property
    def is_navigation_page(self):
        return is_navigation_page(self)

    @property
    def host(self):
        return urlsplit(self.url).netloc.lower()

    @property
    def path(self):
        return urlsplit(self.url).path or "/"

    @property
    def query(self):
        return urlsplit(self.url).query


def _extract_title_episode_from_page_title(page_title):
    """Generic extraction used by many detectors as a fallback.

    Handles patterns like:
        'Watch One Piece Episode 1089 English Subbed'   -> ('One Piece', 1089)
        'One Piece | Netflix Official Site'             -> ('One Piece', None)
        'Watching One Piece on Crunchyroll'             -> ('One Piece', None)
    """
    if not page_title:
        return None, None
    t = page_title.strip()

    ep = parse_episode(t)
    m = re.search(r"\b(?:Episode|EP)\s+(\d{1,4})\b", t, re.I)
    head = t[: m.start()] if m else re.split(r"\s*[|]\s*", t)[0]

    head = re.sub(r"^(?:Watch|Watching|Stream|Streaming)\s+", "", head, flags=re.I)
    head = re.sub(
        r"\s*(?:English|Japanese)\s*(?:Sub|Dub)(?:bed)?\s*$", "", head, flags=re.I
    )
    head = re.sub(r"\s+\b(?:on|at)\b\s+\S+\s*$", "", head, flags=re.I)
    head = strip_trailing_season(head).strip(" -–|")
    if not head:
        head = re.split(r"\s*[|]\s*", t)[0].strip()
    return (head or None), ep


# --------------------------------------------------------------------------- #
# Base class
# --------------------------------------------------------------------------- #
class BaseDetector:
    name = "generic"
    hosts = []  # list[re.Pattern] matched against the hostname

    def matches(self, url):
        host = urlsplit(url).netloc.lower()
        return any(p.search(host) for p in self.hosts)

    def is_navigation(self, ctx):
        """True when this tab is a browse/home page that must be ignored.

        Detectors may override this when their site uses a path segment that
        looks like navigation but is actually part of a watch URL.
        """
        return ctx.is_navigation_page

    def detect(self, ctx):  # pragma: no cover - abstract
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# Concrete detectors
# --------------------------------------------------------------------------- #
class CrunchyrollDetector(BaseDetector):
    """Crunchyroll: metadata lives in the __NEXT_DATA__ JSON embedded in the page."""
    name = "Crunchyroll"
    hosts = [re.compile(r"(?:^|\.)crunchyroll\.com$")]

    def detect(self, ctx):
        parsed = self._from_next_data(ctx)
        if parsed:
            series_title, episode = parsed
            return Detection(
                self.name, ctx.url,
                raw_title=ctx.document_title,
                cleaned_title=series_title,
                episode=episode,
                source="next_data",
            )
        # Fallback: page title ('Watch One Piece on Crunchyroll').
        title, ep = _extract_title_episode_from_page_title(ctx.document_title)
        if title:
            return Detection(
                self.name, ctx.url,
                raw_title=ctx.document_title,
                cleaned_title=title,
                episode=ep,
                source="page",
            )
        return None

    @staticmethod
    def _from_next_data(ctx):
        """Locate the episode object inside the page's __NEXT_DATA__ blob."""
        if not ctx.next_data:
            return None
        try:
            data = json.loads(ctx.next_data)
        except (json.JSONDecodeError, TypeError):
            return None

        found = []

        def walk(node):
            if isinstance(node, dict):
                is_episode = node.get("__typename") == "Episode" or (
                    "episodeNumber" in node and ("seriesId" in node or "series" in node)
                )
                if is_episode:
                    episode = node.get("episodeNumber")
                    series = node.get("seriesTitle")
                    media = node.get("media") or node.get("series")
                    if isinstance(media, dict) and not series:
                        title = media.get("title")
                        if isinstance(title, dict):
                            series = title.get("romaji") or title.get("english") or title.get("native")
                        elif isinstance(title, str):
                            series = title
                    if isinstance(series, str) and series:
                        found.append((series, episode))
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(data)
        if not found:
            return None
        series, episode = found[0]
        return series, episode


class HidiveDetector(BaseDetector):
    """HIDIVE puts the whole path in the URL: /stream/<title>/season-N/<ep>."""
    name = "HIDIVE"
    hosts = [re.compile(r"(?:^|\.)hidive\.com$")]

    def detect(self, ctx):
        m = re.search(r"^/stream/(?P<title>[^/]+)/(?P<season>[^/]+)/(?P<ep>\d+)(?:/|$)", ctx.path)
        if m:
            return Detection(
                self.name, ctx.url,
                raw_title=ctx.document_title,
                cleaned_title=clean_slug(m.group("title")),
                episode=int(m.group("ep")),
                source="url",
            )
        title, ep = _extract_title_episode_from_page_title(ctx.document_title)
        if title:
            return Detection(self.name, ctx.url, raw_title=ctx.document_title,
                             cleaned_title=title, episode=ep, source="page")
        return None


class GoGoAnimeDetector(BaseDetector):
    """GoGoAnime mirrors: /<slug>-episode-<n>."""
    name = "GoGoAnime"
    hosts = [re.compile(r"(?:^|\.)(?:gogoanime|gogo-stream|gogoplay)\w*\.")]

    def detect(self, ctx):
        m = re.search(r"^/(?P<slug>.+?)-episode-(?P<ep>\d+)(?:/|$)", ctx.path, re.I)
        if m:
            return Detection(
                self.name, ctx.url,
                raw_title=ctx.document_title,
                cleaned_title=clean_slug(m.group("slug")),
                episode=int(m.group("ep")),
                source="url",
            )
        title, ep = _extract_title_episode_from_page_title(ctx.document_title)
        if title:
            return Detection(self.name, ctx.url, raw_title=ctx.document_title,
                             cleaned_title=title, episode=ep, source="page")
        return None


class AnimePaheDetector(BaseDetector):
    """AnimePahe: /an/<slug>/<ep> or /an/<slug>."""
    name = "AnimePahe"
    hosts = [re.compile(r"(?:^|\.)animepahe\.(?:ru|com|net|org)")]

    def detect(self, ctx):
        m1 = re.search(r"^/(?:an|a)/(?P<slug>[^/]+)/(?P<ep>\d+)(?:/|$)", ctx.path)
        m2 = re.search(r"^/(?:an|a)/(?P<slug>[^/]+)/?(?:/|$)", ctx.path)
        m = m1 or m2
        if m:
            ep = int(m.group("ep")) if (m1 and m1.group("ep")) else None
            return Detection(
                self.name, ctx.url,
                raw_title=ctx.document_title,
                cleaned_title=clean_slug(m.group("slug")),
                episode=ep or parse_episode(ctx.document_title),
                source="url" if m1 else "slug",
            )
        title, ep = _extract_title_episode_from_page_title(ctx.document_title)
        if title:
            return Detection(self.name, ctx.url, raw_title=ctx.document_title,
                             cleaned_title=title, episode=ep, source="page")
        return None


class AniWave9AnimeDetector(BaseDetector):
    """AniWave / 9anime: /watch/<slug> with an ?ep= query param."""
    name = "AniWave"
    hosts = [re.compile(r"(?:^|\.)(?:aniwave|9anime)\w*\.")]

    def detect(self, ctx):
        params = parse_qs(ctx.query)
        ep = int(params.get("ep", [""])[0]) if params.get("ep") and params["ep"][0].isdigit() else None

        title, t_ep = _extract_title_episode_from_page_title(ctx.document_title)
        if t_ep and ep is None:
            ep = t_ep
        source = "page"
        if not title:
            m = re.search(r"^/watch/(?P<slug>.+?)/?(?:/|$)", ctx.path)
            if m:
                slug = re.sub(r"\.[A-Za-z0-9]{6,}$", "", m.group("slug"))  # strip site id
                title = clean_slug(slug)
                source = "slug"
        if not title:
            return None
        website = "AniWave" if "aniwave" in ctx.host else "9anime"
        return Detection(website, ctx.url, raw_title=ctx.document_title,
                         cleaned_title=title, episode=ep, source=source)


class HiAnimeDetector(BaseDetector):
    """HiAnime / AniWatch / Zoro.to: /watch/<slug> with ?ep=."""
    name = "HiAnime"
    hosts = [re.compile(r"(?:^|\.)(?:hianime|aniwatch|zoro)\w*\.")]

    def detect(self, ctx):
        params = parse_qs(ctx.query)
        ep = int(params.get("ep", [""])[0]) if params.get("ep") and params["ep"][0].isdigit() else None
        title, t_ep = _extract_title_episode_from_page_title(ctx.document_title)
        if t_ep and ep is None:
            ep = t_ep
        source = "page"
        if not title:
            m = re.search(r"^/watch/(?P<slug>.+?)/?(?:/|$)", ctx.path)
            if m:
                title = clean_slug(m.group("slug"))
                source = "slug"
        if not title:
            return None
        website = "HiAnime" if ("hianime" in ctx.host or "aniwatch" in ctx.host) else "Zoro"
        return Detection(website, ctx.url, raw_title=ctx.document_title,
                         cleaned_title=title, episode=ep, source=source)


class NetflixDetector(BaseDetector):
    """Netflix: title comes from the page title (DRM hides episode numbers)."""
    name = "Netflix"
    hosts = [re.compile(r"(?:^|\.)netflix\.com$")]

    def detect(self, ctx):
        t = ctx.document_title or ""
        m = (
            re.search(r"^Watching\s+(?P<title>.+?)\s*[|]\s*", t, re.I)
            or re.search(r"^Watch\s+(?P<title>.+?)\s*[|]\s*", t, re.I)
            or re.search(r"^(?P<title>.+?)\s*[|]\s*Netflix", t, re.I)
        )
        title = m.group("title").strip() if m else (re.sub(r"\s*[|].*$", "", t).strip() if t else None)
        title = strip_trailing_season(title) if title else None
        if not title:
            return None
        episode = parse_episode(t) or parse_episode(title)
        return Detection(self.name, ctx.url, raw_title=t, cleaned_title=title,
                         episode=episode, source="page")


class HuluDetector(BaseDetector):
    """Hulu: title from the page title (DRM hides episode numbers)."""
    name = "Hulu"
    hosts = [re.compile(r"(?:^|\.)hulu\.com$")]

    def detect(self, ctx):
        t = ctx.document_title or ""
        m = re.search(r"^Watch\s+(?P<title>.+?)\s+(?:Streaming|Online)", t, re.I) \
            or re.search(r"^(?P<title>.+?)\s*[|]\s*", t)
        title = m.group("title").strip() if m else (re.sub(r"\s*[|].*$", "", t).strip() if t else None)
        title = strip_trailing_season(title) if title else None
        if not title:
            return None
        episode = parse_episode(t) or parse_episode(title)
        return Detection(self.name, ctx.url, raw_title=t, cleaned_title=title,
                         episode=episode, source="page")


class BilibiliDetector(BaseDetector):
    """Bilibili.tv: /play/bilibili/<id> with the title in the page title."""
    name = "Bilibili"
    hosts = [re.compile(r"(?:^|\.)bilibili\.(?:tv|com)")]

    def detect(self, ctx):
        title, ep = _extract_title_episode_from_page_title(ctx.document_title)
        if not title:
            return None
        return Detection(self.name, ctx.url, raw_title=ctx.document_title,
                         cleaned_title=title, episode=ep, source="page")


class MiruroDetector(BaseDetector):
    """Miruro: /watch/<anilistId>/<slug>?ep=<n>.

    Miruro's URLs expose the AniList ID directly (the site is AniList-based),
    so matching is near-perfect: we hand the ID straight to AniList instead of
    fuzzy-searching the title.
    """
    name = "Miruro"
    hosts = [re.compile(r"(?:^|\.)miruro\.\w+")]

    def detect(self, ctx):
        anilist_id = None
        title = None
        source = "page"

        m = re.search(r"^/watch/(?P<id>\d+)/(?P<slug>[^/?]+)/?(?:/|$)", ctx.path)
        if not m:
            m = re.search(r"^/info/(?P<id>\d+)/(?P<slug>[^/?]+)/?(?:/|$)", ctx.path)
        if m:
            anilist_id = int(m.group("id"))
            title = clean_slug(m.group("slug"))
            source = "url"
        else:
            m = re.search(r"^/info(?:/|$)", ctx.path)
            if m:
                params = parse_qs(ctx.query)
                if params.get("id") and params["id"][0].isdigit():
                    anilist_id = int(params["id"][0])

        params = parse_qs(ctx.query)
        ep = int(params["ep"][0]) if (params.get("ep") and params["ep"][0].isdigit()) else None

        if not title:
            title, t_ep = _extract_title_episode_from_page_title(ctx.document_title)
            if title:
                title = re.sub(r"\s*-\s*Miruro\s*$", "", title, flags=re.I).strip()
            if t_ep and ep is None:
                ep = t_ep

        if ep is None:
            ep = parse_episode(ctx.document_title)

        if not title and anilist_id is None:
            return None

        return Detection(self.name, ctx.url, raw_title=ctx.document_title,
                         cleaned_title=title, episode=ep, source=source,
                         anilist_id=anilist_id)


class AniPmDetector(BaseDetector):
    """ani.pm: ``/anime/<slug>-<id>[-<token>]`` and ``/ani/<slug>-<anilistId>-<token>``.

    Episode pages add ``/episode-<n>``; links from listings sometimes carry an
    opaque ``?ep=`` token instead, in which case the episode number comes from
    the player heading/selected episode or document title. The newer ``/ani/``
    route embeds the AniList ID, so
    those detections skip the fuzzy title search.
    """
    name = "ani.pm"
    hosts = [re.compile(r"(?:^|\.)ani\.pm$")]

    _EPISODE_PATH = re.compile(r"/episode-(?P<ep>\d{1,4})/?$", re.I)
    _ANI_ROUTE = re.compile(
        r"^/ani/(?P<slug>.+)-(?P<id>\d{1,8})(?:-[A-Za-z0-9]+)?/?$"
    )

    @classmethod
    def _episode_from_path(cls, path):
        m = cls._EPISODE_PATH.search(path.rstrip("/"))
        return int(m.group("ep")) if m else None

    @staticmethod
    def _has_episode_query(ctx):
        params = parse_qs(ctx.query)
        return bool(params.get("ep") and params["ep"][0])

    def is_navigation(self, ctx):
        # ``/anime/<slug>`` is a title page, but the same path with ``?ep=`` is
        # the actual player. The generic "first segment is anime" rule would
        # reject both, so only ignore pages with no selected episode.
        return self._episode_from_path(ctx.path) is None and not self._has_episode_query(ctx)

    @staticmethod
    def _slug_title(slug):
        """Turn an ani.pm title slug into a clean title.

        Drops the site's internal numeric id and the short share token:
            ``star-detective-precure-8630-honbtp`` -> ``Star Detective Precure``
            ``monster-1561``                      -> ``Monster``
        """
        slug = re.sub(r"-\d{1,8}-[A-Za-z0-9]{4,}$", "", slug)
        slug = re.sub(r"-\d{1,8}$", "", slug)
        return clean_slug(slug) or None

    @staticmethod
    def _from_page_title(page_title):
        """Parse ``Watch <Title> Episode N ... · ani.pm`` into (title, episode).

        Returns ``(None, None)`` for generic non-content titles such as the
        site's home-page title.
        """
        t = (page_title or "").strip()
        if not re.match(r"^(?:Watch|Watching)\s+", t, re.I):
            return None, None
        t = re.sub(r"\s*[·|]\s*ani\.pm\s*$", "", t, flags=re.I).strip()
        t = re.sub(r"^(?:Watch|Watching)\s+", "", t, flags=re.I)
        t = re.sub(
            r"\s+(?:English|Japanese)\s+(?:Sub|Dub)(?:bed)?(?:\s*&\s*Dub)?"
            r"(?:\s+Online)?\s+Free\s*$",
            "", t, flags=re.I,
        )
        episode = parse_episode(t)
        m = re.search(r"\s*\b(?:Episode|EP)\.?\s*\d{1,4}\b.*$", t, re.I)
        if m:
            t = t[:m.start()]
        return (t.strip(" -–|·") or None), episode

    def detect(self, ctx):
        path = ctx.path.rstrip("/") or "/"
        path_episode = self._episode_from_path(path)
        has_episode_query = self._has_episode_query(ctx)

        # Title pages have no episode selected, so there is no playback to
        # report (their trailers must not become a presence).
        if path_episode is None and not has_episode_query:
            return None

        route_path = re.sub(r"/episode-\d{1,4}/?$", "", path, flags=re.I) or "/"
        anilist_id = None
        title = None
        source = "slug"

        m = self._ANI_ROUTE.match(route_path)
        if m:
            anilist_id = int(m.group("id"))
            title = clean_slug(m.group("slug")) or None
            source = "url"
        else:
            m = re.match(r"^/anime/(?P<slug>[^/]+)/?$", route_path)
            if m:
                title = self._slug_title(m.group("slug"))

        page_title, page_episode = self._from_page_title(ctx.document_title)
        if page_title:
            title = page_title
            source = "page"

        # DOM metadata is live across SPA episode changes; document titles can
        # still describe the previous episode. Never interpret the opaque ?ep=
        # identifier as an ordinal (even if it happens to contain only digits).
        dom_episode = ctx.episode
        if (isinstance(dom_episode, bool) or not isinstance(dom_episode, (int, str))
                or not re.fullmatch(r"[0-9]{1,4}", str(dom_episode))
                or int(dom_episode) <= 0):
            dom_episode = None
        else:
            dom_episode = int(dom_episode)
        episode = path_episode if path_episode is not None else (dom_episode or page_episode)
        episode_title = ""
        if dom_episode is not None and dom_episode == episode and isinstance(ctx.episode_title, str):
            episode_title = " ".join(ctx.episode_title.split())[:128]
            if re.fullmatch(r"(?:Episode|EP)\.?\s*\d+", episode_title, re.I):
                episode_title = ""
        if not title and anilist_id is None:
            return None
        return Detection(
            self.name, ctx.url,
            raw_title=ctx.document_title,
            cleaned_title=title,
            episode=episode,
            source=source,
            anilist_id=anilist_id,
            episode_title=episode_title,
        )


# --------------------------------------------------------------------------- #
# Generic fallback for "known anime site" hosts without a bespoke parser
# --------------------------------------------------------------------------- #
KNOWN_SITES = [
    (re.compile(r"(?:^|\.)primevideo\.com$"), "Prime Video"),
    (re.compile(r"(?:^|\.)disneyplus\.com$"), "Disney+"),
    (re.compile(r"(?:^|\.)wco(?:stream|fun)?\."), "WcoFun"),
    (re.compile(r"(?:^|\.)watchcartoononline"), "WatchCartoonOnline"),
    (re.compile(r"(?:^|\.)kissanime\w*\."), "KissAnime"),
    (re.compile(r"(?:^|\.)animetwist\w*\."), "AnimeTwist"),
    (re.compile(r"(?:^|\.)animedao\w*\."), "AnimeDao"),
    (re.compile(r"(?:^|\.)animekisa\w*\."), "AnimeKisa"),
    (re.compile(r"(?:^|\.)gogoanime\w*\."), "GoGoAnime"),
    (re.compile(r"(?:^|\.)kaa\w*\."), "KickAssAnime"),

]


def _generic_detect(ctx, site_name):
    """Generic page-title + URL-slug extraction shared by known/custom sites."""
    title, ep = _extract_title_episode_from_page_title(ctx.document_title)
    source = "page"
    if not title:
        segments = [s for s in ctx.path.split("/") if s]
        if segments:
            title = clean_slug(segments[-1])
            source = "slug"
    if not title:
        return None
    return Detection(site_name, ctx.url, raw_title=ctx.document_title,
                     cleaned_title=title, episode=ep, source=source)


class GenericKnownSiteDetector(BaseDetector):
    """Best-effort extraction for sites covered only by KNOWN_SITES."""
    name = "GenericAnimeSite"
    hosts = [pattern for pattern, _ in KNOWN_SITES]

    def _site_name(self, ctx):
        for pattern, name in KNOWN_SITES:
            if pattern.search(ctx.host):
                return name
        return ctx.host.split(".")[-2].title() if ctx.host else "Anime Site"

    def detect(self, ctx):
        return _generic_detect(ctx, self._site_name(ctx))


def make_custom_host_pattern(host):
    """Turn a user-supplied domain (``myanime.to``) into a host regex that also
    matches any subdomain. Returns ``None`` when *host* is unusable."""
    host = re.sub(r"^https?://", "", (host or "").strip().lower())
    host = host.split("/")[0].strip(".")
    if not host or not re.match(r"^[a-z0-9]([a-z0-9.-]+)?[a-z0-9]$", host):
        return None
    return re.compile(r"(?:^|\.)" + re.escape(host) + r"$")


class CustomSiteDetector(BaseDetector):
    """Best-effort extraction for streaming sites the user adds in Settings.

    Host matching is identical to :class:`GenericKnownSiteDetector`; only the
    display name comes from the user (or the domain when no name is given).
    """
    name = "CustomSite"
    hosts = []

    def __init__(self, sites=None):
        self.sites = []  # [("name", re.Pattern), ...]
        self.set_sites(sites or [])

    def set_sites(self, sites):
        self.sites = []
        self.hosts = []
        for entry in sites or []:
            name = str(entry.get("name") or "").strip()
            host = str(entry.get("host") or "").strip()
            pattern = make_custom_host_pattern(host)
            if not pattern:
                continue
            self.sites.append(((name or host.split(".")[-2].title()), pattern))
            self.hosts.append(pattern)

    def _site_name(self, ctx):
        for name, pattern in self.sites:
            if pattern.search(ctx.host):
                return name
        return ctx.host.split(".")[-2].title() if ctx.host else "Custom Site"

    def detect(self, ctx):
        return _generic_detect(ctx, self._site_name(ctx))


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
class DetectorRegistry:
    """Ordered list of detectors; the first matching site wins."""

    def __init__(self, custom_sites=None):
        self.detectors = [
            CrunchyrollDetector(),
            HidiveDetector(),
            NetflixDetector(),
            HuluDetector(),
            GoGoAnimeDetector(),
            AniWave9AnimeDetector(),
            HiAnimeDetector(),
            AnimePaheDetector(),
            MiruroDetector(),
            AniPmDetector(),
            BilibiliDetector(),
            GenericKnownSiteDetector(),
            CustomSiteDetector(custom_sites),
        ]

    def set_custom_sites(self, sites):
        """Replace the user-added site list; detection picks it up on the next poll."""
        self.detectors[-1].set_sites(sites)

    def detect(self, ctx):
        """Return the first successful :class:`Detection`, or None."""
        for detector in self.detectors:
            if not detector.matches(ctx.url):
                continue
            # Home/browse/search pages autoplay trailer videos that would look
            # like a real <video> to the pipeline; there's no anime to identify
            # there. Detectors may relax this for sites whose watch URLs share a
            # path segment with their browse pages (ani.pm's /anime/<slug>).
            if detector.is_navigation(ctx):
                logger.debug("skipping navigation page %s", ctx.url)
                return None
            try:
                detection = detector.detect(ctx)
            except Exception:  # noqa: BLE001 - one broken site must not kill the app
                logger.exception("detector %s raised", detector.name)
                detection = None
            if detection:
                return detection
        return None
