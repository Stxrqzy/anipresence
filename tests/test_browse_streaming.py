import json
from src.core.streaming import anime_url, valid_search_template
from src.config import ConfigManager
from src.anime import browse


def test_streaming_destination_preserves_matching_episode_and_encodes_search():
    entry = {"title": "A & B / 日本", "url": "https://anilist.co/anime/1"}
    assert anime_url(entry) == "https://ani.pm/anime?q=A%20%26%20B%20%2F%20%E6%97%A5%E6%9C%AC"
    assert anime_url(dict(entry, url="https://ani.pm/watch/first?ep=2")) == "https://ani.pm/watch/first?ep=2"
    assert anime_url(entry, "Crunchyroll").startswith("https://www.crunchyroll.com/search?")
    assert anime_url(entry, "Custom", "javascript:{query}").startswith("https://ani.pm/")
    assert valid_search_template("https://example.com/search/{query}")
    assert not valid_search_template("https://example.com/")
    assert "evil" not in anime_url(dict(entry, url="https://ani.pm.evil.com/watch/1"))


def test_config_migrates_navigation_and_named_artwork_once(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"current_tab": 4, "accent": "#f472b6"}))
    cfg = ConfigManager(path)
    assert cfg.data.current_tab == 5
    assert cfg.data.artwork_theme == "Cherry Blossom"
    cfg.save()
    assert ConfigManager(path).data.current_tab == 5


def test_browse_studio_pagination_and_combined_filters(monkeypatch):
    calls = []
    closed = []
    class Client:
        def __init__(self):
            self._session = type("Session", (), {"close": lambda _: closed.append(True)})()
        def _query(self, query, variables):
            calls.append((query, variables.copy()))
            if "studios(search:" in query:
                return {"Page": {"studios": [{"id": 100, "name": "Test Studio"}]}}
            if "Studio(id:" in query:
                page = variables["page"]
                return {"Studio": {"media": {"pageInfo": {"hasNextPage": page == 1}, "nodes": [{"id": page}]}}}
            assert variables["ids"] == [1, 2]
            assert variables["page"] == 3 and variables["year"] == 2025
            assert variables["adult"] is False
            assert "id_in: $ids" in query
            return {"Page": {"pageInfo": {"hasNextPage": False, "total": 1},
                             "media": [{"id": 2, "title": {"english": "Example"}, "episodes": 12}]}}
    browse._studio_cache.clear()
    monkeypatch.setattr(browse, "AniListClient", Client)
    result = browse.catalogue({"studio": "Test Studio", "year": 2025, "adult": False}, page=3)
    assert result["entries"][0]["title"] == "Example"
    assert len(calls) == 4 and closed == [True]
    browse._studio_cache.clear()
