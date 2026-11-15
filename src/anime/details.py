"""Public anime details. All network work is invoked from a background worker."""
import html
import re
from .anilist import AniListClient
from ..core.media_identity import media_id

QUERY = """
query ($id: Int, $search: String) {
  Media(id: $id, search: $search, type: ANIME) {
    id title { english romaji native } synonyms
    description(asHtml: false) coverImage { extraLarge large } bannerImage
    episodes duration meanScore genres format status seasonYear season
    startDate { year month day } tags { name isAdult }
    nextAiringEpisode { episode airingAt }
    studios(isMain: true) { nodes { name } }
    relations { edges { relationType node {
      id type isAdult title { english romaji native }
      coverImage { extraLarge large } episodes seasonYear
    } } }
    recommendations(perPage: 18, sort: RATING_DESC) { nodes { mediaRecommendation {
      id type isAdult title { english romaji native } coverImage { extraLarge large }
      episodes seasonYear meanScore genres format
    } } }
  }
}
"""


def plain_description(value):
    plain = html.unescape(re.sub(r"<[^>]+>", "", re.sub(r"<br\s*/?>", "\n", value or "", flags=re.I)))
    return re.sub(r"\n\s*\n+", "\n\n", plain).strip()


def entry_from_media(media):
    titles = media.get("title") or {}
    return {"anilist_id": media["id"],
            "title": titles.get("english") or titles.get("romaji") or titles.get("native") or "Unknown",
            "title_english": titles.get("english") or "",
            "aliases": [*filter(None, titles.values()), *(media.get("synonyms") or [])],
            "cover": (media.get("coverImage") or {}).get("extraLarge") or (media.get("coverImage") or {}).get("large") or "",
            "banner_url": media.get("bannerImage") or "",
            "episodes": media.get("episodes"), "season_year": media.get("seasonYear"),
            "mean_score": media.get("meanScore"), "genres": media.get("genres") or [], "format": media.get("format")}


def load_details(entry, hide_adult=True):
    client = AniListClient()
    try:
        identity = media_id(entry)
        variables = {"id": identity} if identity else {"search": entry.get("title_english") or entry.get("title")}
        media = client._query(QUERY, variables).get("Media")
        if not media:
            raise ValueError("Anime not found")
        result = entry_from_media(media)
        result.update(description=plain_description(media.get("description")), genres=media.get("genres") or [],
                      mean_score=media.get("meanScore"), format=media.get("format"), status=media.get("status"),
                      duration=media.get("duration"),
                      next_episode=(media.get("nextAiringEpisode") or {}).get("episode"),
                      airing_at=(media.get("nextAiringEpisode") or {}).get("airingAt"),
                      season=media.get("season"), start_date=media.get("startDate") or {},
                      native_title=(media.get("title") or {}).get("native") or "",
                      tags=[t["name"] for t in media.get("tags") or [] if not (hide_adult and t.get("isAdult"))],
                      studios=[s["name"] for s in (media.get("studios") or {}).get("nodes") or []])
        result["related"] = [
            dict(entry_from_media(edge["node"]), relation=edge["relationType"].replace("_", " ").title())
            for edge in (media.get("relations") or {}).get("edges") or []
            if edge["node"].get("type") == "ANIME" and not (hide_adult and edge["node"].get("isAdult"))]
        result["recommendations"] = [
            entry_from_media(node["mediaRecommendation"])
            for node in (media.get("recommendations") or {}).get("nodes") or []
            if node.get("mediaRecommendation") and node["mediaRecommendation"].get("type") == "ANIME"
            and not (hide_adult and node["mediaRecommendation"].get("isAdult"))]
        return result
    finally:
        client._session.close()
