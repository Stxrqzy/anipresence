from src.anime.details import load_details, plain_description
from src.anime.tmdb import TMDBClient
from src.config import ConfigManager


def test_details_identity_relations_and_plain_synopsis(monkeypatch):
    closed = []
    class Client:
        def __init__(self):
            self._session = type("Session", (), {"close": lambda _: closed.append(True)})()
        def _query(self, query, variables):
            assert variables == {"id": 42}
            return {"Media": {"id": 42, "title": {"english": "Example"},
                              "description": "<b>Story</b><br>One &amp; two",
                              "episodes": 24, "relations": {"edges": [
                                  {"relationType": "SEQUEL", "node": {"id": 43, "type": "ANIME", "title": {"romaji": "Next"}}},
                                  {"relationType": "SOURCE", "node": {"id": 44, "type": "MANGA"}},
                                  {"relationType": "OTHER", "node": {"id": 45, "type": "ANIME", "isAdult": True}},
                              ]}}}
    monkeypatch.setattr("src.anime.details.AniListClient", Client)
    result = load_details({"anilist_id": 42, "title": "Alias"})
    assert result["description"] == "Story\nOne & two"
    assert [e["anilist_id"] for e in result["related"]] == [43]
    assert result["related"][0]["relation"] == "Sequel"
    assert closed == [True]


def test_logo_prefers_english_png_then_language_neutral(monkeypatch):
    client = TMDBClient("test")
    logos = [{"file_path": "/ja.png", "iso_639_1": "ja", "width": 3000},
             {"file_path": "/neutral.png", "iso_639_1": None},
             {"file_path": "/en.png", "iso_639_1": "en", "width": 500},
             {"file_path": "/vector.svg", "iso_639_1": "en", "vote_average": 10}]
    monkeypatch.setattr(client, "get", lambda *args, **kw: {"logos": logos})
    show = {"tmdb_id": 1, "tmdb_kind": "tv"}
    assert client.logo(show).endswith("/en.png")
    logos.pop(2)
    assert client.logo(show).endswith("/neutral.png")
    logos.clear()
    assert client.logo(show) == ""


def test_session_migration_keeps_wrong_profile_token_out_of_active_slot(tmp_path):
    cfg = ConfigManager(tmp_path / "config.json")
    cfg.set(anilist_username="Bob", anilist_sync_account="Alice", anilist_access_token="alice-test")
    loaded = ConfigManager(cfg.path)
    assert loaded.data.anilist_sessions == {"alice": "alice-test"}
    assert loaded.data.anilist_access_token == ""
    assert loaded.data.anilist_sync_account == ""
