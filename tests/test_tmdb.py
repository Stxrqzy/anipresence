import pytest
from src.anime.tmdb import TMDBClient
from src.core.artwork import ArtworkService


@pytest.mark.parametrize("credential,is_key", [("a"*32, True), ("read-access-token", False)])
def test_tmdb_auth_supports_keys_and_read_access_tokens(monkeypatch, credential, is_key):
    client = TMDBClient(credential)
    def get(url, **kwargs):
        assert url == "https://api.themoviedb.org/3/search/tv"
        assert (kwargs["params"].get("api_key") == credential) is is_key
        assert kwargs["headers"] == ({} if is_key else {"Authorization": "Bearer "+credential})
        return type("Response", (), {"raise_for_status": lambda self: None,
                                     "json": lambda self: {"results": []}})()
    monkeypatch.setattr(client.session, "get", get)
    assert client.get("/search/tv", query="Example") == {"results": []}


def test_tmdb_matches_alias_year_and_maps_absolute_episode(monkeypatch):
    client = TMDBClient("token")
    calls = []
    def get(path, **kwargs):
        calls.append(path)
        if path == "/search/tv":
            return {"results": [{"id": 20, "name": "Naruto", "first_air_date": "2002-10-03",
                                 "genre_ids": [16], "poster_path": "/naruto.jpg"}]}
        if path == "/search/movie":
            return {"results": []}
        if path == "/tv/20":
            return {"seasons": [{"season_number": 0, "episode_count": 2},
                                {"season_number": 1, "episode_count": 52, "air_date": "2002-10-03"},
                                {"season_number": 2, "episode_count": 52, "air_date": "2003-10-03"}]}
        if path == "/tv/20/season/2": return {}
        assert path == "/tv/20/season/2/episode/44"
        return {"name": "Deadlock! Sannin Showdown!", "still_path": "/episode.jpg"}
    monkeypatch.setattr(client, "get", get)
    show = client.match({"title": "NARUTO", "season_year": 2002})
    assert show["season"] is None
    episode = client.episode(show, 96)
    assert episode["episode_title"] == "Deadlock! Sannin Showdown!"
    assert episode["episode_art"].endswith("/original/episode.jpg")


def test_tmdb_ambiguous_remakes_and_non_animation_use_fallback(monkeypatch):
    client = TMDBClient("token")
    items = [{"id": 1, "name": "Hunter x Hunter", "genre_ids": [16], "first_air_date": "1999-01-01"},
             {"id": 2, "name": "Hunter x Hunter", "genre_ids": [16], "first_air_date": "2011-01-01"},
             {"id": 3, "name": "Hunter x Hunter", "genre_ids": [18], "first_air_date": "2011-01-01"}]
    monkeypatch.setattr(client, "get", lambda path, **kw: {"results": items} if path == "/search/tv" else {})
    assert client.match({"title": "Hunter x Hunter"}) == {}
    assert client.match({"title": "Hunter x Hunter", "season_year": 2011})["tmdb_id"] == 2


def test_tmdb_explicit_season_uses_local_episode_number(monkeypatch):
    client = TMDBClient("token")
    def get(path, **kwargs):
        if path == "/search/tv":
            assert kwargs["query"] == "Example"
            return {"results": [{"id": 2, "name": "Example", "genre_ids": [16]}]}
        if path == "/search/movie": return {}
        if path == "/tv/2": return {"seasons": []}
        if path == "/tv/2/season/3": return {}
        if path.endswith("/images"): return {}
        assert path == "/tv/2/season/3/episode/2"
        return {"name": "Episode name", "still_path": None}
    monkeypatch.setattr(client, "get", get)
    show = client.match({"title": "Example 3rd Season"})
    assert client.episode(show, 2) == {"episode_title": "Episode name", "episode_art": ""}


@pytest.fixture
def artwork(tmp_path, monkeypatch):
    # Run queued work explicitly so tests can inspect the pre-completion state.
    class Thread:
        def __init__(self, **kwargs): pass
        def start(self): pass
    monkeypatch.setattr("src.core.artwork.threading.Thread", Thread)
    return ArtworkService(tmp_path, lambda: None)


def drain(service):
    service.jobs.put((99999, next(service.sequence), None))
    service._run()


def test_artwork_is_tmdb_first_cached_and_uses_original_on_image_failure(artwork, monkeypatch):
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QImage, QColor
    image = QImage(8, 8, QImage.Format_ARGB32); image.fill(QColor("red"))
    buffer = QBuffer(); buffer.open(QIODevice.WriteOnly); image.save(buffer, "PNG")
    content = bytes(buffer.data())
    calls = []
    class Session:
        def get(self, url, **kwargs):
            calls.append(url)
            return type("Response", (), {"content": content if "poster" in url else b"bad-image",
                                         "raise_for_status": lambda self: None})()
        def close(self): pass
    class Client:
        def __init__(self, credential): self.session = Session()
        def match(self, entry): return {"poster": "https://image.tmdb.org/t/p/w500/poster.jpg", "tmdb_id": 20}
        def episode(self, show, episode): return {"episode_title": "The episode", "episode_art": "https://image.tmdb.org/t/p/w780/broken.jpg"}
    monkeypatch.setattr("src.core.artwork.TMDBClient", Client)
    entry = {"title": "Naruto", "anilist_id": 20, "cover": "anilist.jpg", "episode": 3}
    assert artwork.enrich(entry, episode=True) == entry
    artwork.configure("secret")
    assert artwork.enrich(entry, episode=True) == entry
    artwork.enrich(entry, episode=True)
    assert artwork.jobs.qsize() == 1
    drain(artwork)
    result = artwork.enrich(entry, episode=True)
    assert "tmdb.org" in result["cover"]
    assert result["anilist_cover"] == "anilist.jpg"
    assert "episode_art" not in result
    assert result["episode_title"] == "The episode"
    assert entry["cover"] == "anilist.jpg"
    assert "secret" not in (artwork.directory / "tmdb.json").read_text()
    restored = ArtworkService(artwork.directory, lambda: None)
    restored.configure("secret")
    assert restored.enrich(entry, episode=True) == result
    assert len(calls) == 2
    artwork.configure("secret", "anilist")
    chosen = artwork.enrich(result, episode=True)
    assert chosen["cover"] == "anilist.jpg"
    assert chosen["episode_title"] == "The episode"
    artwork.configure("")
    assert artwork.enrich(entry, episode=True) == entry


def test_network_failure_is_throttled_and_new_credentials_retry(artwork, monkeypatch):
    calls = []
    class Client:
        def __init__(self, credential):
            self.session = type("Session", (), {"close": lambda self: None})()
        def match(self, entry):
            calls.append(entry)
            raise OSError("offline")
    monkeypatch.setattr("src.core.artwork.TMDBClient", Client)
    entry = {"title": "Example", "cover": "anilist", "episode": 1}
    artwork.configure("first")
    artwork.enrich(entry, episode=True); drain(artwork)
    assert artwork.enrich(entry, episode=True) == entry
    artwork.enrich(dict(entry, episode=2), episode=True); drain(artwork)
    assert len(calls) == 1
    artwork.configure("second")
    artwork.enrich(entry, episode=True); drain(artwork)
    assert len(calls) == 2


def test_old_credential_response_is_discarded(artwork, monkeypatch):
    class Client:
        def __init__(self, credential):
            self.session = type("Session", (), {"close": lambda self: None})()
        def match(self, entry):
            artwork.configure("new-token")
            return {"tmdb_id": 123}
    monkeypatch.setattr("src.core.artwork.TMDBClient", Client)
    artwork.configure("old-token")
    artwork.enrich({"title": "Example"}); drain(artwork)
    assert artwork.cache == {}


def test_absolute_numbered_tmdb_season_and_local_sequel(monkeypatch):
    client = TMDBClient("token")
    episodes = [{"episode_number": n, "name": f"Chapter {n}", "still_path": "/still.jpg"}
                for n in range(1156, 1181)]
    monkeypatch.setattr(client, "get", lambda *args, **kw: {"episodes": episodes})
    show = {"tmdb_id": 37854, "tmdb_kind": "tv", "seasons": [
        {"season_number": 1, "episode_count": 1155}, {"season_number": 23, "episode_count": 25}]}
    assert client.episode(show, 1162)["episode_title"] == "Chapter 1162"
    assert client.episode(dict(show, season=23), 7)["episode_title"] == "Chapter 1162"


def test_episodic_entry_prefers_tv_over_identically_named_movie(monkeypatch):
    client = TMDBClient("token")
    def get(path, **kw):
        if path.startswith("/search/"):
            return {"results": [{"id": 1 if path.endswith("tv") else 2,
                                  "name": "Black Clover", "genre_ids": [16]}]}
        return {"seasons": []}
    monkeypatch.setattr(client, "get", get)
    assert client.match({"title": "Black Clover", "episode": 6})["tmdb_kind"] == "tv"


def test_visible_requests_jump_ahead_and_promote_queued_work(artwork, monkeypatch):
    calls = []
    class Client:
        def __init__(self, credential):
            self.session = type("Session", (), {"close": lambda self: None})()
        def match(self, entry):
            calls.append(entry["title"])
            return {}
    monkeypatch.setattr("src.core.artwork.TMDBClient", Client)
    artwork.configure("token")
    artwork.enrich({"title": "Old history"}, priority=50)
    artwork.enrich({"title": "Visible poster"}, priority=50)
    artwork.enrich({"title": "Visible poster"}, priority=5)
    artwork.enrich({"title": "Playing"}, priority=0)
    drain(artwork)
    assert calls == ["Playing", "Visible poster", "Old history"]
