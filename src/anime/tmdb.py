"""TMDB artwork and episode metadata, with conservative anime matching."""
import re
import requests
from rapidfuzz.fuzz import ratio
from ..core.media_identity import normalized_title


class TMDBClient:
    def __init__(self, credential, timeout=10):
        self.session = requests.Session()
        self.credential = credential.strip()
        self.timeout = timeout

    def get(self, path, **params):
        headers = {}
        if re.fullmatch(r"[a-fA-F0-9]{32}", self.credential):
            params["api_key"] = self.credential
        else:
            headers["Authorization"] = "Bearer " + self.credential
        response = self.session.get("https://api.themoviedb.org/3" + path,
                                    params={"language": "en-US", **params}, headers=headers,
                                    timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def image(path, size="w500"):
        return f"https://image.tmdb.org/t/p/{size}{path}" if path and path.startswith("/") else ""

    def match(self, entry):
        titles = list(dict.fromkeys(filter(None, [entry.get("title"), *(entry.get("aliases") or [])])))
        season_match = re.search(r"(?:season\s*(\d+)|(\d+)(?:st|nd|rd|th)\s+season)",
                                 entry.get("title") or "", re.I)
        season = int(season_match[1] or season_match[2]) if season_match else None
        queries = [re.sub(r"\s*:?\s*(?:season\s*\d+|\d+(?:st|nd|rd|th)\s+season)\s*$", "", t, flags=re.I)
                   for t in titles]
        candidates = {}
        for query in queries[:3]:
            for kind in ("tv", "movie"):
                for item in self.get(f"/search/{kind}", query=query, include_adult="false").get("results") or []:
                    if 16 not in (item.get("genre_ids") or []):
                        continue
                    names = [item.get("name"), item.get("original_name"), item.get("title"), item.get("original_title")]
                    score = max(ratio(normalized_title(q), normalized_title(n)) for q in queries for n in names if n)
                    year = str(entry.get("season_year") or "")
                    candidate_year = str(item.get("first_air_date") or item.get("release_date") or "")[:4]
                    if year and candidate_year == year:
                        score += 8
                    candidates[(kind, item["id"])] = (score, item)
        ranked = sorted(candidates.items(), key=lambda pair: pair[1][0], reverse=True)
        if not ranked or ranked[0][1][0] < 88:
            return {}
        if len(ranked) > 1 and ranked[0][1][0] - ranked[1][1][0] < 3:
            return {}  # remakes / ambiguous matches retain AniList art
        (kind, identity), (_, item) = ranked[0]
        result = {"tmdb_id": identity, "tmdb_kind": kind,
                  "poster": self.image(item.get("poster_path")),
                  "backdrop": self.image(item.get("backdrop_path"), "w780")}
        if kind == "tv":
            details = self.get(f"/tv/{identity}")
            seasons = [s for s in details.get("seasons") or [] if s.get("season_number", 0) > 0]
            if (not season and entry.get("season_year")
                    and str(entry["season_year"]) != str(item.get("first_air_date") or "")[:4]):
                matches = [s for s in seasons if str(s.get("air_date") or "").startswith(str(entry["season_year"]))]
                if len(matches) == 1:
                    season = matches[0]["season_number"]
            result.update(seasons=seasons, season=season)
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
        data = self.get(f"/tv/{show['tmdb_id']}/season/{season}/episode/{number}")
        return {"episode_title": data.get("name") or "",
                "episode_art": self.image(data.get("still_path"), "w780")}
