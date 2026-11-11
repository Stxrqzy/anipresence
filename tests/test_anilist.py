"""AniList client helpers: Media normalization and adult-content detection."""
import pytest

from src.anime.anilist import AniListClient, AniListError, Media


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
        ({"tags": ["NSFW"]}, True),
        ({"genres": ["Action", "Adventure"]}, False),
        ({"tags": [{"name": "Action", "isAdult": False}]}, False),
        ({"genres": ["Comedy"], "tags": ["School Life"]}, False),
        # "Adult" alone must not flag: the tag "Adult Cast" is common on
        # mainstream workplace/slice-of-life anime.
        ({"genres": ["Drama"], "tags": ["Adult Cast"]}, False),
        # Content-warning tags are not 18+: Vinland Saga carries "Sexual
        # Violence" but is a mainstream seinen, not hentai.
        (
            {"genres": ["Action", "Drama", "Adventure"], "tags": ["Seinen", "Historical", "Sexual Violence", "Gore"]},
            False,
        ),
        # Vinland Saga's full real tag set (incl. the "Nudity" warning tag)
        # must not flag: "Nudity" is a generic content warning shared with
        # mainstream shows, not a marker of adult content.
        (
            {
                "genres": ["Action", "Adventure", "Drama"],
                "tags": [
                    "Vikings", "Seinen", "Historical", "Military", "Gore",
                    "Nudity", "Slavery", "Revenge", "Primarily Adult Cast",
                ],
            },
            False,
        ),
        # An ecchi title with no genre but the unambiguous tag is still caught.
        ({"genres": ["Comedy"], "tags": ["Ecchi", "School"]}, True),
        # "sex" must not match inside "asexual"/"sexual" as a substring.
        ({"tags": ["Asexual", "Sexual Violence"]}, False),
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
            "episodes": 12,
            "season": "SUMMER",
            "seasonYear": 2024,
            "nextAiringEpisode": {"airingAt": 1723000000, "episode": 8},
            "genres": ["Action"],
            "isAdult": False,
            "tags": [{"name": "Ecchi", "isAdult": False}],
        }
    )
    assert nested.title_romaji == "Sword Art Online"
    assert nested.cover_large == "https://c/l.jpg"
    assert nested.episodes == 12
    assert nested.season == "SUMMER"
    assert nested.season_year == 2024
    assert nested.next_airing_at == 1723000000
    assert nested.next_airing_episode == 8
    assert nested.is_adult_content() is True

    flat = Media.from_dict(
        {
            "id": 9,
            "title_romaji": "Flat",
            "cover_large": "https://c/f.jpg",
            "episodes": 24,
            "season": "WINTER",
            "season_year": 2023,
            "next_airing_at": 1723000000,
            "next_airing_episode": 13,
            "tags": [],
        }
    )
    assert flat.title_romaji == "Flat"
    assert flat.cover_large == "https://c/f.jpg"
    assert flat.episodes == 24
    assert flat.season == "WINTER"
    assert flat.season_year == 2023
    assert flat.next_airing_at == 1723000000
    assert flat.next_airing_episode == 13


def test_from_dict_missing_airing_is_safe():
    media = Media.from_dict({"id": 1, "title": {"romaji": "Old"}, "episodes": 12})
    assert media.next_airing_at is None
    assert media.next_airing_episode is None
    assert media.season == ""
    assert media.season_year is None


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


def test_parse_list_collection_shape():
    parsed = AniListClient._parse_list_collection(
        {
            "MediaListCollection": {
                "lists": [
                    {
                        "entries": [
                            {
                                "status": "COMPLETED",
                                "progress": 12,
                                "media": {"id": 3, "title": {"romaji": "X"}},
                            }
                        ]
                    }
                ]
            }
        }
    )
    assert parsed[0]["progress"] == 12
    assert parsed[0]["status"] == "COMPLETED"
    assert parsed[0]["media"].id == 3


def test_fetch_viewer_list_requires_token():
    client = AniListClient()
    with pytest.raises(AniListError):
        client.fetch_viewer_list()


def test_custom_lists_do_not_duplicate_imported_progress():
    entry = {"status": "CURRENT", "progress": 4, "media": {"id": 42, "title": {"romaji": "A"}}}
    parsed = AniListClient._parse_list_collection({"MediaListCollection": {
        "lists": [{"entries": [entry]}, {"entries": [entry]}]}})
    assert len(parsed) == 1


def test_fetch_viewer_list_parses_collection(monkeypatch):
    def fake_query(query, variables):
        if "Viewer" in query:
            return {"Viewer": {"id": 7, "name": "Alice"}}
        assert variables == {"userId": 7}
        return {
            "MediaListCollection": {
                "lists": [
                    {
                        "entries": [
                            {
                                "status": "CURRENT",
                                "progress": 4,
                                "media": {"id": 1, "title": {"romaji": "Show"}, "episodes": 12},
                            }
                        ]
                    }
                ]
            }
        }

    client = AniListClient(access_token="tok")
    monkeypatch.setattr(client, "_query", fake_query)
    entries, name = client.fetch_viewer_list()
    assert name == "Alice"
    assert entries[0]["status"] == "CURRENT"
    assert entries[0]["progress"] == 4
    assert entries[0]["media"].id == 1


def test_sync_progress_does_not_regress_existing_entry(monkeypatch):
    calls = []

    def fake_query(query, variables):
        calls.append((query, variables))
        if query.lstrip().startswith("query"):
            return {"Viewer": {"id": 1}, "Media": {
                "mediaListEntry": {"progress": 7, "status": "CURRENT"}}}
        raise AssertionError("a lower progress must not be submitted")

    client = AniListClient(access_token="token")
    monkeypatch.setattr(client, "_query", fake_query)
    assert client.sync_progress(42, 5, 12) is False
    assert len(calls) == 1


def test_sync_progress_saves_and_completes(monkeypatch):
    calls = []

    def fake_query(query, variables):
        calls.append((query, variables))
        if query.lstrip().startswith("query"):
            return {"Viewer": {"id": 1}, "Media": {
                "mediaListEntry": {"progress": 2, "status": "CURRENT"}}}
        return {"SaveMediaListEntry": {"progress": 3, "status": "COMPLETED"}}

    client = AniListClient(access_token="token")
    monkeypatch.setattr(client, "_query", fake_query)
    assert client.sync_progress(42, 3, 3) is True
    assert calls[1][1] == {"mediaId": 42, "progress": 3, "status": "COMPLETED"}


def test_sync_new_entry_authenticated_lookup(monkeypatch):
    requests = []

    def post(url, **kwargs):
        requests.append(kwargs)
        query = kwargs["json"]["query"]
        if "Viewer" in query:
            assert "mediaListEntry" in query
            data = {"Viewer": {"id": 1}, "Media": {"episodes": 12, "mediaListEntry": None}}
        else:
            data = {"SaveMediaListEntry": {"progress": 1, "status": "CURRENT"}}
        return type("Response", (), {"status_code": 200, "json": lambda self: {"data": data}})()

    client = AniListClient(access_token="test-token")
    monkeypatch.setattr(client._session, "post", post)
    assert client.sync_progress(42, 1)
    assert all(r["headers"]["Authorization"] == "Bearer test-token" for r in requests)
    assert requests[-1]["json"]["variables"]["status"] == "CURRENT"


@pytest.mark.parametrize("status", ["CURRENT","REPEATING","PLANNING","COMPLETED","PAUSED","DROPPED"])
def test_library_status_edit_preserves_progress_and_checks_viewer(monkeypatch,status):
    client=AniListClient(access_token="token");calls=[]
    def query(text,variables):
        calls.append((text,variables))
        if "Viewer" in text:return {"Viewer":{"id":1,"name":"Alice"}}
        assert variables=={"mediaId":42,"status":status}
        assert "progress:" not in text
        return {"SaveMediaListEntry":{"id":9,"status":status,"progress":7}}
    monkeypatch.setattr(client,"_query",query)
    assert client.change_list_status(42,status,"alice")["progress"]==7
    assert len(calls)==2
    with pytest.raises(AniListError,match="Connect"):
        client.change_list_status(42,status,"Bob")
    assert len(calls)==3  # identity query only; no cross-account mutation


def test_remove_library_entry_uses_authenticated_entry_id(monkeypatch):
    client=AniListClient(access_token="token");calls=[]
    def query(text,variables):
        calls.append((text,variables))
        if "Viewer" in text:return {"Viewer":{"id":1,"name":"Alice"}}
        if "mediaListEntry" in text:return {"Media":{"mediaListEntry":{"id":1234}}}
        assert variables=={"id":1234}
        return {"DeleteMediaListEntry":{"deleted":True}}
    monkeypatch.setattr(client,"_query",query)
    assert client.change_list_status(42,"REMOVE","Alice")=={"removed":True}
    assert len(calls)==3
