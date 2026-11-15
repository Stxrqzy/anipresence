"""Public AniList catalogue queries; no invented streaming availability."""
from .anilist import AniListClient
import time
import threading
import json
import copy
from collections import OrderedDict
from ..core.media_identity import media_id

_studio_cache = {}
_studio_lock = threading.Lock()
_query_cache = OrderedDict()
_query_lock = threading.Lock()


def _cached(key):
    with _query_lock:
        value=_query_cache.get(key)
        if value and time.monotonic()-value[0]<600:
            _query_cache.move_to_end(key)
            return copy.deepcopy(value[1])
        _query_cache.pop(key,None)


def _remember(key,result):
    with _query_lock:
        _query_cache[key]=(time.monotonic(),copy.deepcopy(result))
        while len(_query_cache)>48:_query_cache.popitem(last=False)
    return result


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
    key=("catalogue",json.dumps(filters,sort_keys=True),page)
    cached=_cached(key)
    if cached is not None:return cached
    return _remember(key,_catalogue(filters,page))


def _catalogue(filters, page=1):
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


def recommendation_seeds(library):
    eligible=[e for e in library if media_id(e) and
              (e.get("library_section") in ("Watching","Completed") or e.get("status") in ("CURRENT","COMPLETED","REPEATING"))]
    eligible.sort(key=lambda e:(e.get("score") or 0,e.get("progress") or 0,e.get("mean_score") or 0),reverse=True)
    return list(dict.fromkeys(media_id(e) for e in eligible))[:4]


def recommendations(library, hide_adult=True):
    """One bounded public query, seeded by the user's watched/watching titles."""
    from .details import entry_from_media
    seeds=recommendation_seeds(library)
    if not seeds:return {"entries":[],"hasNextPage":False,"note":"Add watching or completed anime to your AniList library for personal recommendations."}
    excluded={media_id(e) for e in library if media_id(e)}
    key=("recommendations",tuple(seeds),tuple(sorted(excluded)),hide_adult)
    cached=_cached(key)
    if cached is not None:return cached
    fields="""id type isAdult title { english romaji native } coverImage { extraLarge large }
              episodes meanScore genres seasonYear format"""
    query="query ("+", ".join(f"$id{i}: Int" for i in range(len(seeds)))+") {"
    for i in range(len(seeds)):
        query+=f"""seed{i}: Media(id: $id{i}, type: ANIME) {{
          title {{ english romaji }} recommendations(perPage: 12, sort: RATING_DESC) {{
          nodes {{ rating mediaRecommendation {{ {fields} }} }} }} }}"""
    query+="}"
    client=AniListClient()
    try:
        response=client._query(query,{f"id{i}":identity for i,identity in enumerate(seeds)})
        candidates={}
        for i in range(len(seeds)):
            seed=response.get(f"seed{i}") or {}
            title=seed.get("title") or {}
            reason=title.get("english") or title.get("romaji") or "your library"
            for node in (seed.get("recommendations") or {}).get("nodes") or []:
                media=node.get("mediaRecommendation") or {}
                identity=media.get("id")
                if not identity or identity in excluded or media.get("type")!="ANIME" or (hide_adult and media.get("isAdult")):continue
                row=candidates.setdefault(identity,dict(entry_from_media(media),recommendation_weight=0,reasons=[]))
                row["recommendation_weight"]+=max(1,node.get("rating") or 0)
                if reason not in row["reasons"]:row["reasons"].append(reason)
        entries=sorted(candidates.values(),key=lambda e:(len(e["reasons"]),e["recommendation_weight"]),reverse=True)[:40]
        for row in entries:row["suggestion_reason"]="Because you watch "+", ".join(row.pop("reasons"))
        result={"entries":entries,"hasNextPage":False,"note":"Based on your watching/completed library · Titles already on your list are excluded."}
        return _remember(key,result)
    finally:
        client._session.close()
