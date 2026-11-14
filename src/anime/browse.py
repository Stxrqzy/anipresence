"""Public AniList catalogue queries; no invented streaming availability."""
from .anilist import AniListClient
import time
import threading

_studio_cache = {}
_studio_lock = threading.Lock()


def studio_media_ids(client, name):
    key = name.casefold()
    with _studio_lock:
        cached = _studio_cache.get(key)
        if cached and time.monotonic()-cached[0] < 900:
            return cached[1]
    data = client._query("query ($search: String) { Page(perPage: 10) { studios(search: $search) { id name } } }",
                        {"search": name})
    studios = (data.get("Page") or {}).get("studios") or []
    studio = next((s for s in studios if s["name"].casefold() == key), None)
    if not studio:
        if len(studios) == 1:
            studio = studios[0]
        else:
            names = ", ".join(s["name"] for s in studios)
            raise ValueError("Choose an exact studio name" + (f": {names}" if names else ". No studios found."))
    ids = []
    page = 1
    while True:
        data = client._query("""query ($id: Int, $page: Int) {
            Studio(id: $id) { media(page: $page, perPage: 50, isMain: true) {
                pageInfo { hasNextPage } nodes { id } } } }""", {"id": studio["id"], "page": page})
        media = (data.get("Studio") or {}).get("media") or {}
        ids.extend(item["id"] for item in media.get("nodes") or [])
        if not (media.get("pageInfo") or {}).get("hasNextPage"): break
        page += 1
    with _studio_lock:
        if len(_studio_cache) >= 32: _studio_cache.pop(next(iter(_studio_cache)))
        _studio_cache[key] = (time.monotonic(), ids)
    return ids

QUERY = """
query ($page: Int, $search: String, $sort: [MediaSort], $genre: String,
       $tag: String, $year: Int, $season: MediaSeason, $format: MediaFormat,
       $status: MediaStatus, $country: CountryCode, $score: Int, $adult: Boolean) {
  Page(page: $page, perPage: 40) {
    pageInfo { total hasNextPage currentPage }
    media(type: ANIME, search: $search, sort: $sort, genre: $genre, tag: $tag,
          seasonYear: $year, season: $season, format: $format, status: $status,
          countryOfOrigin: $country, averageScore_greater: $score, isAdult: $adult) {
      id title { english romaji native } synonyms coverImage { extraLarge large }
      episodes meanScore genres seasonYear siteUrl
    }
  }
}
"""


def catalogue(filters, page=1):
    client = AniListClient()
    try:
        variables = {key: value for key, value in filters.items()
                     if key in ("search", "sort", "genre", "tag", "year", "season", "format", "status", "country", "score", "adult")
                     and value is not None and value != ""}
        variables["page"] = page
        query = QUERY
        if filters.get("studio"):
            variables["ids"] = studio_media_ids(client, filters["studio"])
            if not variables["ids"]:
                return {"entries": [], "total": 0, "hasNextPage": False}
            query = query.replace("$page: Int,", "$ids: [Int], $page: Int,").replace("media(type: ANIME,", "media(id_in: $ids, type: ANIME,")
        data = client._query(query, variables).get("Page") or {}
        rows = []
        for media in data.get("media") or []:
            titles = media.get("title") or {}
            rows.append({"anilist_id": media["id"], "title": titles.get("english") or titles.get("romaji") or titles.get("native") or "Unknown",
                         "title_english": titles.get("english") or "", "aliases": [*filter(None, titles.values()), *(media.get("synonyms") or [])],
                         "cover": (media.get("coverImage") or {}).get("extraLarge") or (media.get("coverImage") or {}).get("large") or "",
                         "episodes": media.get("episodes"), "mean_score": media.get("meanScore"),
                         "genres": media.get("genres") or [], "season_year": media.get("seasonYear"), "url": media.get("siteUrl") or "",
                         "library_section": "", "status": ""})
        return {"entries": rows, **(data.get("pageInfo") or {})}
    finally:
        client._session.close()
