"""AniList GraphQL client.

Wraps the public AniList GraphQL endpoint. Titles are searched with
SEARCH_MATCH sorting, which already performs fuzzy ranking, so the website
extracted title rarely has to match perfectly. Rate limits are handled by
retrying once with backoff; results are cached on disk by the caller.
"""
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Optional

import requests

logger = logging.getLogger("anipresence.anilist")

ANILIST_ENDPOINT = "https://graphql.anilist.co"

_SEARCH_QUERY = """
query ($search: String, $perPage: Int) {
  Page(page: 1, perPage: $perPage) {
    media(search: $search, type: ANIME, sort: SEARCH_MATCH) {
      id
      title { romaji english native }
      synonyms
      coverImage { extraLarge large }
      description(asHtml: false)
      siteUrl
      episodes
      season
      seasonYear
      nextAiringEpisode { airingAt episode }
      meanScore
      genres
      isAdult
      tags { name isAdult }
    }
  }
}
"""

_MEDIA_QUERY = """
query ($id: Int) {
  Media(id: $id, type: ANIME) {
    id
    title { romaji english native }
    synonyms
    coverImage { extraLarge large }
    description(asHtml: false)
    siteUrl
    episodes
    season
    seasonYear
    nextAiringEpisode { airingAt episode }
    meanScore
    genres
    isAdult
    tags { name isAdult }
  }
}
"""

# Public user list: works without OAuth as long as the list is public. Includes
# progress (episodes watched) so the stats dashboard can be seeded from history.
_USER_LIST_QUERY = """
query ($name: String) {
  MediaListCollection(userName: $name, type: ANIME) {
    lists {
      entries {
        status
        progress
        media {
          id
          title { romaji english native }
          coverImage { extraLarge large }
          siteUrl
          episodes
          season
          seasonYear
          nextAiringEpisode { airingAt episode }
          meanScore
          isAdult
          genres
          tags { name isAdult }
        }
      }
    }
  }
}
"""

# Avatar lookup for the account indicator in the UI (public User profile).
_USER_QUERY = """
query ($name: String) {
  User(name: $name) {
    id
    name
    avatar { large }
  }
}
"""

# Current-season catalogue for the Calendar view (public, no auth needed).
_SEASONAL_QUERY = """
query ($season: MediaSeason, $seasonYear: Int, $perPage: Int) {
  Page(page: 1, perPage: $perPage) {
    media(
      season: $season
      seasonYear: $seasonYear
      type: ANIME
      sort: POPULARITY_DESC
      isAdult: false
    ) {
      id
      title { romaji english native }
      coverImage { large extraLarge }
      siteUrl
      episodes
      season
      seasonYear
      nextAiringEpisode { airingAt episode }
      meanScore
      genres
      tags { name isAdult }
      isAdult
    }
  }
}
"""


class AniListError(Exception):
    """Raised when the AniList API rejects a request."""


@dataclass
class Media:
    """A normalized AniList anime entry."""
    id: int
    title_romaji: str = ""
    title_english: str = ""
    title_native: str = ""
    synonyms: list = field(default_factory=list)
    cover_large: str = ""
    cover_extra_large: str = ""
    description: str = ""
    site_url: str = ""
    episodes: Optional[int] = None
    season: str = ""              # "WINTER"/"SPRING"/"SUMMER"/"FALL"
    season_year: Optional[int] = None
    next_airing_at: Optional[int] = None   # unix epoch of the next episode
    next_airing_episode: Optional[int] = None
    mean_score: Optional[int] = None
    genres: list = field(default_factory=list)
    is_adult: bool = False
    tags: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, d):
        d = d or {}
        title = d.get("title") or {}
        cover = d.get("coverImage") or {}
        # as_dict() serializes the dataclass flat (title_romaji / cover_large /
        # cover_extra_large), while AniList GraphQL uses nested shapes. Accept both.
        flat = "title_romaji" in d
        next_airing = d.get("nextAiringEpisode") or {}
        return cls(
            id=d.get("id"),
            title_romaji=(title.get("romaji") if not flat else d.get("title_romaji")) or "",
            title_english=(title.get("english") if not flat else d.get("title_english")) or "",
            title_native=(title.get("native") if not flat else d.get("title_native")) or "",
            synonyms=list(d.get("synonyms") or []),
            cover_large=(
                (cover.get("large") or cover.get("extraLarge"))
                if not flat else d.get("cover_large")
            ) or "",
            cover_extra_large=(
                cover.get("extraLarge") if not flat else d.get("cover_extra_large")
            ) or "",
            description=d.get("description") or "",
            site_url=d.get("siteUrl") or d.get("site_url") or "",
            episodes=d.get("episodes"),
            season=(d.get("season") if not flat else d.get("season")) or "",
            season_year=d.get("seasonYear", d.get("season_year")),
            next_airing_at=(
                next_airing.get("airingAt")
                if not flat else d.get("next_airing_at")
            ),
            next_airing_episode=(
                next_airing.get("episode")
                if not flat else d.get("next_airing_episode")
            ),
            mean_score=d.get("meanScore", d.get("mean_score")),
            genres=list(d.get("genres") or []),
            is_adult=bool(d.get("isAdult") or d.get("is_adult")),
            tags=list(d.get("tags") or []),
        )

    def display_title(self):
        return self.title_romaji or self.title_english or self.title_native

    # Genres are a small controlled vocabulary on AniList, so scanning them
    # broadly is safe: in practice only "Ecchi" (or a full hentai title) ever
    # matches, and no mainstream show gets a genre that trips these.
    _ADULT_GENRE_TERMS = (
        "hentai", "ecchi", "erotica", "erotic", "adult", "porn",
        "sex", "smut", "nudity", "nsfw",
    )

    # Tags are free-form and include generic content warnings ("Nudity",
    # "Sexual Violence", "Adult Cast", "Gore"...) that mainstream shows like
    # Vinland Saga carry. Only unambiguous NSFW markers are matched there;
    # anything else would hide non-adult anime. AniList's own isAdult flag is
    # the authoritative signal for true 18+ content.
    _ADULT_TAG_TERMS = ("ecchi", "hentai", "smut", "nsfw")

    def is_adult_content(self):
        """True when the title is 18+ or ecchi/NSFW-adjacent."""
        if self.is_adult:
            return True
        genres = " ".join(self.genres).lower()
        for tag in self.tags:
            name = (tag.get("name") or "") if isinstance(tag, dict) else str(tag)
            if any(
                re.search(rf"\b{re.escape(term)}\b", name.lower())
                for term in self._ADULT_TAG_TERMS
            ):
                return True
        return any(
            re.search(rf"\b{re.escape(term)}\b", genres)
            for term in self._ADULT_GENRE_TERMS
        )


class AniListClient:
    """Thin, retrying GraphQL client for AniList."""

    def __init__(self, timeout=12):
        self._timeout = timeout
        self._session = requests.Session()

    def _query(self, query, variables):
        try:
            resp = self._session.post(
                ANILIST_ENDPOINT,
                json={"query": query, "variables": variables},
                timeout=self._timeout,
            )
            payload = resp.json()
        except requests.RequestException as exc:
            raise AniListError(f"network error: {exc}") from exc

        if "errors" in payload:
            errs = payload["errors"]
            msg = errs[0].get("message", "unknown AniList error")
            # AniList enforces a ~90 req/min limit; one polite retry with backoff.
            if "rate" in msg.lower() or resp.status_code == 429:
                logger.info("AniList rate limit hit, retrying in 3s")
                import time
                time.sleep(3)
                return self._query(query, variables)
            raise AniListError(msg)

        return payload.get("data", {})

    def search_media(self, search, per_page=5):
        """Search anime by title. Returns up to `per_page` :class:`Media`."""
        variables = {"search": search, "perPage": per_page}
        data = self._query(_SEARCH_QUERY, variables)
        raw_list = (data.get("Page") or {}).get("media") or []
        return [Media.from_dict(item) for item in raw_list]

    def get_media(self, media_id):
        """Fetch a single anime by its AniList ID, or None if it doesn't exist."""
        data = self._query(_MEDIA_QUERY, {"id": int(media_id)})
        raw = data.get("Media")
        return Media.from_dict(raw) if raw else None

    def fetch_user_list(self, username):
        """Fetch a public user's anime list for stats backfill / section tabs.

        Returns a list of dicts:
            {"media": Media, "status": str, "progress": int}
        Every standard list membership (current/repeating/planning/completed/
        paused/dropped) is included so the caller can group by status.
        """
        data = self._query(_USER_LIST_QUERY, {"name": username})
        lists = (data.get("MediaListCollection") or {}).get("lists") or []
        results = []
        for group in lists:
            for entry in group.get("entries") or []:
                raw = entry.get("media") or {}
                results.append(
                    {
                        "media": Media.from_dict(raw) if raw else None,
                        "status": entry.get("status"),
                        "progress": entry.get("progress") or 0,
                    }
                )
        return results

    def fetch_seasonal(self, season, year, per_page=50):
        """Fetch the top anime for a given season (e.g. ("SUMMER", 2026)).

        Returns a list of :class:`Media` (adult titles excluded). Episodes that
        have finished airing carry no ``nextAiringEpisode`` info.
        """
        data = self._query(
            _SEASONAL_QUERY,
            {"season": season, "seasonYear": int(year), "perPage": int(per_page)},
        )
        raw_list = (data.get("Page") or {}).get("media") or []
        return [Media.from_dict(item) for item in raw_list]

    def fetch_user_profile(self, username):
        """Fetch a public user's avatar URL, or "" if the user has no avatar.

        Raises :class:`AniListError` when the username doesn't resolve.
        """
        data = self._query(_USER_QUERY, {"name": username})
        user = data.get("User") or {}
        avatar = (user.get("avatar") or {}).get("large") or ""
        return avatar

    def as_dict(self, media):
        """Convert a Media dataclass into a JSON-serializable dict for caching."""
        return asdict(media)
