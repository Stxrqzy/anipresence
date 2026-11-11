"""AniList GraphQL client.

Wraps the public AniList GraphQL endpoint. Titles are searched with
SEARCH_MATCH sorting, which already performs fuzzy ranking, so the website
extracted title rarely has to match perfectly. Rate limits are handled by
retrying once with backoff; results are cached on disk by the caller.
"""
import logging
import re
import time
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
      bannerImage
      description(asHtml: false)
      siteUrl
      episodes
      duration
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
    bannerImage
    description(asHtml: false)
    siteUrl
    episodes
    duration
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
        createdAt
        updatedAt
        media {
          id
          title { romaji english native }
          coverImage { extraLarge large }
          siteUrl
          episodes
          duration
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

# Authenticated user list: uses the bearer token so it works for private
# profiles and reflects the token owner's own lists (the two-way sync pull side).
_VIEWER_LIST_QUERY = """
query ($userId: Int) {
  MediaListCollection(userId: $userId, type: ANIME) {
    lists {
      entries {
        status
        progress
        createdAt
        updatedAt
        media {
          id
          title { romaji english native }
          coverImage { extraLarge large }
          siteUrl
          episodes
          duration
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

_ACTIVITY_QUERY = """
query ($userId: Int, $page: Int, $since: Int) {
  Page(page: $page, perPage: 50) {
    pageInfo { hasNextPage }
    activities(userId: $userId, type: ANIME_LIST, sort: ID_DESC, createdAt_greater: $since) {
      ... on ListActivity {
        id status progress createdAt
        media {
          id type title { romaji english native }
          duration episodes seasonYear genres siteUrl coverImage { extraLarge large }
        }
      }
    }
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
    banner_url: str = ""
    duration: Optional[int] = None

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
            banner_url=d.get("bannerImage") or d.get("banner_url") or "",
            duration=d.get("duration"),
        )

    def display_title(self):
        return self.title_english or self.title_romaji or self.title_native

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

    def __init__(self, timeout=12, access_token=""):
        self._timeout = timeout
        self._access_token = (access_token or "").strip()
        self._session = requests.Session()
        self._rate_wait_until = 0

    def set_access_token(self, access_token):
        """Replace the bearer token used by authenticated mutations."""
        self._access_token = (access_token or "").strip()

    def _query(self, query, variables, _retry=True):
        delay = self._rate_wait_until-time.time()
        if delay > 0:
            time.sleep(min(delay, 65))
        try:
            headers = {}
            if self._access_token:
                headers["Authorization"] = f"Bearer {self._access_token}"
            resp = self._session.post(
                ANILIST_ENDPOINT,
                json={"query": query, "variables": variables},
                headers=headers,
                timeout=self._timeout,
            )
            payload = resp.json()
            response_headers = getattr(resp, "headers", {})
            if response_headers.get("X-RateLimit-Remaining") == "0":
                self._rate_wait_until = float(response_headers.get("X-RateLimit-Reset") or time.time()+60)
        except requests.RequestException as exc:
            raise AniListError(f"network error: {exc}") from exc

        if "errors" in payload:
            errs = payload["errors"]
            msg = errs[0].get("message", "unknown AniList error")
            # AniList enforces a ~90 req/min limit; one polite retry with backoff.
            if _retry and ("rate" in msg.lower() or resp.status_code == 429):
                logger.info("AniList rate limit hit, retrying in 3s")
                time.sleep(min(65, max(3, float(response_headers.get("Retry-After") or 3))))
                return self._query(query, variables, _retry=False)
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

    @staticmethod
    def _parse_list_collection(data):
        """Flatten a ``MediaListCollection`` payload into ``{media, status, progress}``."""
        lists = (data.get("MediaListCollection") or {}).get("lists") or []
        results = []
        seen = set()
        for group in lists:
            for entry in group.get("entries") or []:
                raw = entry.get("media") or {}
                if raw.get("id") in seen:
                    continue
                if raw.get("id"):
                    seen.add(raw["id"])
                results.append(
                    {
                        "media": Media.from_dict(raw) if raw else None,
                        "status": entry.get("status"),
                        "progress": entry.get("progress") or 0,
                        "created_at": entry.get("createdAt") or 0,
                        "updated_at": entry.get("updatedAt") or 0,
                    }
                )
        return results

    def fetch_user_list(self, username):
        """Fetch a public user's anime list for stats backfill / section tabs.

        Returns a list of dicts:
            {"media": Media, "status": str, "progress": int}
        Every standard list membership (current/repeating/planning/completed/
        paused/dropped) is included so the caller can group by status.
        """
        data = self._query(_USER_LIST_QUERY, {"name": username})
        return self._parse_list_collection(data)

    def fetch_user_activity(self, username, max_pages=None, previous=None, on_page=None, cancelled=None):
        """Read all available list activity, then incrementally refresh its newest end."""
        user = (self._query(_USER_QUERY, {"name": username}).get("User") or {})
        if not user.get("id"):
            raise AniListError("AniList account not found")
        previous = previous or {}
        if (previous.get("account") or "").casefold() != username.casefold():
            previous = {}
        known = {str(e["id"]): e for e in previous.get("items") or [] if e.get("id")}
        items = dict(known)
        has_next = False
        page = 0
        while max_pages is None or page < max_pages:
            if cancelled and cancelled():
                raise AniListError("Activity refresh cancelled")
            page += 1
            result = self._query(_ACTIVITY_QUERY, {"userId": int(user["id"]), "page": page, "since": 0})
            payload = result.get("Page")
            if not isinstance(payload, dict):
                raise AniListError("AniList activity response was incomplete")
            batch = payload.get("activities") or []
            overlap = any(str(e.get("id")) in known for e in batch)
            for item in batch:
                if item.get("id"):
                    items[str(item["id"])] = item
            has_next = bool((payload.get("pageInfo") or {}).get("hasNextPage"))
            if previous.get("complete") and overlap:
                has_next = False
            snapshot = {"items": list(items.values()), "since": 0, "truncated": has_next,
                        "complete": not has_next, "fetched_at": time.time(), "account": user.get("name") or username}
            if on_page:
                on_page(snapshot, page)
            if not has_next:
                break
            if not batch:
                raise AniListError("AniList returned an empty page before the end of the feed")
        return snapshot

    def fetch_viewer(self):
        """Identify the account that owns this token."""
        if not self._access_token:
            raise AniListError("AniList access token is not configured")
        viewer = (self._query("query { Viewer { id name } }", {}) or {}).get("Viewer") or {}
        if not viewer.get("id") or not viewer.get("name"):
            raise AniListError("AniList token did not authenticate an account")
        return viewer

    def fetch_viewer_list(self):
        """Fetch the authenticated viewer's own list (token required).

        Unlike :meth:`fetch_user_list`, this works for private profiles and
        reflects the token owner's current AniList state - the pull half of the
        two-way sync. Returns ``(entries, viewer_name)`` where *entries* has the
        same shape as :meth:`fetch_user_list`.
        """
        viewer = self.fetch_viewer()
        user_id = viewer.get("id")
        if not user_id:
            raise AniListError("AniList token did not authenticate an account")
        data = self._query(_VIEWER_LIST_QUERY, {"userId": int(user_id)})
        return self._parse_list_collection(data), (viewer.get("name") or "")

    def sync_progress(self, media_id, episode, total_episodes=None):
        """Advance an authenticated user's AniList progress without regressing it.

        Returns ``True`` when the list was updated and ``False`` when it was
        already at least this far along. Raises :class:`AniListError` when the
        token is missing or AniList rejects the mutation.
        """
        if not self._access_token:
            raise AniListError("AniList access token is not configured")
        media_id = int(media_id)
        episode = int(episode)
        if episode <= 0:
            return False

        data = self._query(
            """
            query ($mediaId: Int) {
              Viewer { id }
              Media(id: $mediaId, type: ANIME) {
                episodes
                mediaListEntry { progress status }
              }
            }
            """,
            {"mediaId": media_id},
        )
        if not (data.get("Viewer") or {}).get("id"):
            raise AniListError("AniList token did not authenticate an account")
        media = data.get("Media")
        if not media:
            raise AniListError("Anime not found on AniList")
        total_episodes = media.get("episodes") or total_episodes
        if total_episodes and episode > int(total_episodes):
            raise AniListError("Detected episode exceeds the AniList episode count")
        current = media.get("mediaListEntry") or {}
        if int(current.get("progress") or 0) >= episode:
            return False

        status = "COMPLETED" if total_episodes and episode >= int(total_episodes) else "CURRENT"
        self._query(
            """
            mutation ($mediaId: Int, $progress: Int, $status: MediaListStatus) {
              SaveMediaListEntry(mediaId: $mediaId, progress: $progress, status: $status) {
                progress
                status
              }
            }
            """,
            {"mediaId": media_id, "progress": episode, "status": status},
        )
        return True

    def change_list_status(self, media_id, status, account):
        """Edit only the selected account's list status; preserve its progress."""
        allowed = {"CURRENT", "REPEATING", "PLANNING", "COMPLETED", "PAUSED", "DROPPED", "REMOVE"}
        if status not in allowed or int(media_id) <= 0:
            raise AniListError("Invalid library status or anime")
        viewer = self.fetch_viewer()
        if viewer["name"].casefold() != account.casefold():
            raise AniListError(f"Connect the AniList account {account} in Settings to edit this library.")
        if status == "REMOVE":
            data = self._query("query ($id: Int) { Media(id: $id, type: ANIME) { mediaListEntry { id } } }",
                               {"id": int(media_id)})
            entry = (data.get("Media") or {}).get("mediaListEntry") or {}
            if entry.get("id"):
                result = self._query("mutation ($id: Int) { DeleteMediaListEntry(id: $id) { deleted } }", {"id": entry["id"]})
                if not (result.get("DeleteMediaListEntry") or {}).get("deleted"):
                    raise AniListError("AniList did not remove this entry")
            return {"removed": True}
        data = self._query("""mutation ($mediaId: Int, $status: MediaListStatus) {
            SaveMediaListEntry(mediaId: $mediaId, status: $status) { id status progress updatedAt }
        }""", {"mediaId": int(media_id), "status": status})
        result = data.get("SaveMediaListEntry") or {}
        if result.get("status") != status:
            raise AniListError("AniList did not confirm the status change")
        return result

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
