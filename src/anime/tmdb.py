"""TMDB artwork and episode metadata, with conservative anime matching."""
import re
import time
import requests
from rapidfuzz.fuzz import ratio
from ..core.media_identity import normalized_title


class TMDBClient:
    def __init__(self, credential, timeout=10):
        self.session = requests.Session()
        self.credential = credential.strip()
        self.timeout = timeout
        self.response_cache = {}

    def get(self, path, **params):
        cache_key = path + repr(sorted(params.items()))
        cached = self.response_cache.get(cache_key)
        if cached and time.time()-cached["at"] < 86400:
            return cached["value"]
        headers = {}
        if re.fullmatch(r"[a-fA-F0-9]{32}", self.credential):
            params["api_key"] = self.credential
        else:
            headers["Authorization"] = "Bearer " + self.credential
        response = self.session.get("https://api.themoviedb.org/3" + path,
                                    params={"language": "en-US", **params}, headers=headers,
                                    timeout=self.timeout)
        response.raise_for_status()
        value = response.json()
        self.response_cache[cache_key] = {"at": time.time(), "value": value}
        return value

    @staticmethod
    def image(path, size="original"):
        return f"https://image.tmdb.org/t/p/{size}{path}" if path and path.startswith("/") else ""

    def match(self, entry):
        titles = list(dict.fromkeys(filter(None, [entry.get("title_english"), entry.get("title"), *(entry.get("aliases") or [])])))
        season_match = re.search(r"(?:season\s*(\d+)|(\d+)(?:st|nd|rd|th)\s+season)",
                                 entry.get("title") or "", re.I)
        season = int(season_match[1] or season_match[2]) if season_match else None
        queries = list(dict.fromkeys(re.sub(r"\s*:?\s*(?:season\s*\d+|\d+(?:st|nd|rd|th)\s+season)\s*$", "",
                   re.sub(r"\s*\((?:19|20)\d{2}\)\s*$", "", t), flags=re.I) for t in titles))
        year_match = re.search(r"\((19\d{2}|20\d{2})\)", entry.get("title") or "")
        year = str(entry.get("season_year") or (year_match[1] if year_match else ""))
        def normalized(name):
            return re.sub(r"[^\w]+", " ", normalized_title(name).replace("×", "x")).strip()
        def similarity(names):
            return max((ratio(normalized(q), normalized(n)) for q in queries for n in names if n), default=0)
        candidates = {}
        for query in queries[:3]:
            for kind in ("tv", "movie"):
                for item in self.get(f"/search/{kind}", query=query, include_adult="false").get("results") or []:
                    if 16 not in (item.get("genre_ids") or []):
                        continue
                    names = [item.get("name"), item.get("original_name"), item.get("title"), item.get("original_title")]
                    score = similarity(names)
                    candidate_year = str(item.get("first_air_date") or item.get("release_date") or "")[:4]
                    if year and candidate_year == year:
                        score += 8
                    if kind == "tv" and (season or entry.get("episode") or (entry.get("episodes") or entry.get("episodes_total") or 0) > 1):
                        score += 4
                    candidates[(kind, item["id"])] = (score, item)
        # Romaji names often only appear under TMDB alternative titles.
        for (kind, identity), (score, item) in sorted(candidates.items(), key=lambda pair: pair[1][0], reverse=True)[:3]:
            if score >= 88:
                continue
            try:
                alternate = self.get(f"/{kind}/{identity}/alternative_titles")
                names = [e.get("title") for e in alternate.get("results", alternate.get("titles", []))]
                alternate_score = similarity(names)
                if year and str(item.get("first_air_date") or item.get("release_date") or "")[:4] == year:
                    alternate_score += 8
                if kind == "tv" and (season or entry.get("episode") or (entry.get("episodes") or entry.get("episodes_total") or 0) > 1):
                    alternate_score += 4
                score = max(score, alternate_score)
                candidates[(kind, identity)] = (score, item)
            except requests.RequestException:
                pass
        ranked = sorted(candidates.items(), key=lambda pair: pair[1][0], reverse=True)
        if not ranked or ranked[0][1][0] < 88:
            return {}
        if len(ranked) > 1 and ranked[0][1][0] - ranked[1][1][0] < 3:
            return {}  # remakes / ambiguous matches retain AniList art
        (kind, identity), (_, item) = ranked[0]
        result = {"tmdb_id": identity, "tmdb_kind": kind,
                  "title_english": entry.get("title_english") or item.get("name") or item.get("title") or "",
                  "poster": self.image(item.get("poster_path")),
                  "backdrop": self.image(item.get("backdrop_path"))}
        if season and season > 1 and not entry.get("title_english"):
            result["title_english"] += f" Season {season}"
        if kind == "tv":
            details = self.get(f"/tv/{identity}")
            seasons = [s for s in details.get("seasons") or [] if s.get("season_number", 0) > 0]
            if (not season and entry.get("season_year")
                    and str(entry["season_year"]) != str(item.get("first_air_date") or "")[:4]):
                matches = [s for s in seasons if str(s.get("air_date") or "").startswith(str(entry["season_year"]))]
                if len(matches) == 1:
                    season = matches[0]["season_number"]
            result.update(seasons=seasons, season=season)
            if season:
                season_info = next((s for s in seasons if s["season_number"] == season), {})
                if season_info.get("poster_path"):
                    result["poster"] = self.image(season_info["poster_path"])
        return result

    def episode(self, show, episode):
        if show.get("tmdb_kind") != "tv" or not episode:
            return {}
        try:
            number = int(episode)
        except (ValueError, TypeError):
            return {}
        if number <= 0:
            return {}
        season = show.get("season")
        absolute = number
        explicit_season = bool(season)
        if not season:
            # Main-series absolute numbering (e.g. Naruto / One Piece).
            for candidate in sorted(show.get("seasons") or [], key=lambda s: s["season_number"]):
                count = candidate.get("episode_count") or 0
                if number <= count:
                    season = candidate["season_number"]
                    break
                number -= count
        if not season:
            return {}
        # Fetch a season once: feed ranges can contain hundreds of episodes.
        season_data = self.get(f"/tv/{show['tmdb_id']}/season/{season}")
        episodes = sorted(season_data.get("episodes") or [], key=lambda e: e.get("episode_number") or 0)
        data = None if explicit_season else next((e for e in episodes if e.get("episode_number") == absolute), None)
        if data is None and 0 < number <= len(episodes):
            data = episodes[number-1]
        if data is None:
            data = self.get(f"/tv/{show['tmdb_id']}/season/{season}/episode/{number}")
        number = data.get("episode_number") or number
        still = data.get("still_path")
        if not still:
            try:
                images = self.get(f"/tv/{show['tmdb_id']}/season/{season}/episode/{number}/images",
                                  include_image_language="en,null").get("stills") or []
                if images:
                    still = max(images, key=lambda image: image.get("width") or 0).get("file_path")
            except requests.RequestException:
                pass
        return {"episode_title": data.get("name") or "",
                "episode_art": self.image(still)}
