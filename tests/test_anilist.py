"""AniList client helpers: Media normalization and adult-content detection."""
import pytest

from src.anime.anilist import AniListClient, Media


def _media(**kw):
    defaults = {
        "id": 1,
        "title_romaji": "Example",
        "genres": ["Action"],
        "tags": [],
    }
    defaults.update(kw)
    return Media(**defaults)


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"is_adult": True}, True),
        ({"genres": ["Ecchi"]}, True),
        ({"genres": ["Action"], "tags": [{"name": "Ecchi", "isAdult": False}]}, True),
        ({"genres": ["Hentai"]}, True),
        ({"genres": ["Adult"]}, True),
        ({"tags": ["NSFW"]}, True),
        ({"genres": ["Action", "Adventure"]}, False),
        ({"tags": [{"name": "Action", "isAdult": False}]}, False),
        ({"genres": ["Comedy"], "tags": ["School Life"]}, False),
    ],
)
def test_is_adult_content(kwargs, expected):
    assert _media(**kwargs).is_adult_content() is expected


def test_from_dict_flat_and_nested():
    nested = Media.from_dict(
        {
            "id": 9,
            "title": {"romaji": "Sword Art Online", "english": "SAO"},
            "coverImage": {"large": "https://c/l.jpg", "extraLarge": "https://c/xl.jpg"},
            "siteUrl": "https://anilist.co/anime/9",
            "genres": ["Action"],
            "isAdult": False,
            "tags": [{"name": "Ecchi", "isAdult": False}],
        }
    )
    assert nested.title_romaji == "Sword Art Online"
    assert nested.cover_large == "https://c/l.jpg"
    assert nested.is_adult_content() is True

    flat = Media.from_dict(
        {
            "id": 9,
            "title_romaji": "Flat",
            "cover_large": "https://c/f.jpg",
            "tags": [],
        }
    )
    assert flat.title_romaji == "Flat"
    assert flat.cover_large == "https://c/f.jpg"


def test_from_dict_empty_is_safe():
    media = Media.from_dict(None)
    assert media.display_title() == ""
    assert media.is_adult_content() is False


def test_client_query_url(monkeypatch):
    calls = {}

    def fake_post(self, url, **kw):
        calls["url"] = url
        return type("R", (), {"json": lambda *a: {"data": {}}, "status_code": 200})()

    monkeypatch.setattr("requests.Session.post", fake_post)
    client = AniListClient(timeout=5)
    client._query("{ }", {})
    assert calls["url"] == "https://graphql.anilist.co"


def test_fetch_user_profile_returns_avatar(monkeypatch):
    def fake_query(query, variables):
        assert variables == {"name": "Alice"}
        assert "User(name: $name)" in query
        return {"User": {"id": 1, "name": "Alice", "avatar": {"large": "https://img/a.png"}}}

    client = AniListClient()
    monkeypatch.setattr(client, "_query", fake_query)
    assert client.fetch_user_profile("Alice") == "https://img/a.png"


def test_fetch_user_profile_no_avatar(monkeypatch):
    def fake_query(query, variables):
        return {"User": {"id": 2, "name": "Bob", "avatar": None}}

    client = AniListClient()
    monkeypatch.setattr(client, "_query", fake_query)
    assert client.fetch_user_profile("Bob") == ""
