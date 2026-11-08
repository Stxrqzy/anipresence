"""ani.pm detector: path-based episodes, AniList IDs on /ani/ URLs, and the
opaque ?ep= links used by the site's episode lists."""
import pytest

from src.browser.detectors import DetectorRegistry, PageContext

REGISTRY = DetectorRegistry()


def detect(url, title):
    return REGISTRY.detect(PageContext(url=url, document_title=title))


def test_ani_route_embeds_anilist_id_and_episode_path():
    det = detect(
        "https://ani.pm/ani/the-iceblade-sorcerer-shall-rule-the-world-ii-212503-pgnwzk/episode-5",
        "Watch The Iceblade Sorcerer Shall Rule the World II Episode 5 "
        "English Sub & Dub Online Free · ani.pm",
    )
    assert det is not None
    assert det.website == "ani.pm"
    assert det.cleaned_title == "The Iceblade Sorcerer Shall Rule the World II"
    assert det.episode == 5
    assert det.anilist_id == 212503


def test_anime_route_strips_internal_id_and_share_token():
    det = detect(
        "https://ani.pm/anime/star-detective-precure-8630-honbtp/episode-37",
        "Watch Star Detective Precure! Episode 37 English Sub Online Free · ani.pm",
    )
    assert det is not None
    assert det.cleaned_title == "Star Detective Precure!"
    assert det.episode == 37
    assert det.anilist_id is None


def test_legacy_title_slug_without_share_token():
    det = detect(
        "https://ani.pm/anime/monster-1561/episode-5",
        "Watch Monster Episode 5 English Sub Online Free · ani.pm",
    )
    assert det is not None
    assert det.cleaned_title == "Monster"
    assert det.episode == 5


def test_opaque_episode_query_uses_page_title():
    det = detect(
        "https://ani.pm/anime/star-detective-precure-8630?ep=e1.AOqb6k6_Nl6",
        "Watch Star Detective Precure! Episode 37 English Sub Online Free · ani.pm",
    )
    assert det is not None
    assert det.cleaned_title == "Star Detective Precure!"
    assert det.episode == 37


def test_slug_fallback_when_page_title_is_generic():
    det = detect(
        "https://ani.pm/ani/magic-knight-rayearth-2026-178868-mgqpfe/episode-5",
        "ani.pm — watch anime free, subbed & dubbed, in HD",
    )
    assert det is not None
    assert det.cleaned_title
    assert det.episode == 5
    assert det.anilist_id == 178868


def test_title_and_browse_pages_are_ignored():
    assert detect(
        "https://ani.pm/anime/star-detective-precure-8630-honbtp",
        "Watch Star Detective Precure! English Sub Online Free · ani.pm",
    ) is None
    assert detect("https://ani.pm/anime", "Anime · ani.pm") is None
    assert detect(
        "https://ani.pm/",
        "ani.pm — watch anime free, subbed & dubbed, in HD",
    ) is None


def test_opaque_query_uses_live_dom_episode_over_stale_title():
    det = REGISTRY.detect(PageContext(
        url="https://ani.pm/anime/naruto-123?ep=e1.opaque",
        document_title="Watch Naruto Episode 4 · ani.pm",
        episode=5,
    ))
    assert det.episode == 5


@pytest.mark.parametrize("episode", [None, True, -1, 0, 2.5, "e1.token", "10000", {}])
def test_invalid_dom_episode_is_not_reported(episode):
    det = REGISTRY.detect(PageContext(
        url="https://ani.pm/anime/naruto-123?ep=123456",
        document_title="Watch Naruto · ani.pm",
        episode=episode,
    ))
    assert det is not None
    assert det.episode is None  # opaque query IDs must never become episode numbers


def test_explicit_path_episode_takes_precedence_during_navigation():
    det = REGISTRY.detect(PageContext(
        url="https://ani.pm/anime/naruto-123/episode-6",
        document_title="Watch Naruto Episode 5 · ani.pm",
        episode=5,
        episode_title="Previous episode name",
    ))
    assert det.episode == 6
    assert det.episode_title == ""


@pytest.mark.parametrize("name", [None, {}, ["Name"], "Episode 5", "EP. 5"])
def test_invalid_or_number_only_episode_title_is_ignored(name):
    det = REGISTRY.detect(PageContext(
        url="https://ani.pm/anime/naruto-123?ep=e1.opaque",
        document_title="Watch Naruto · ani.pm", episode=5, episode_title=name,
    ))
    assert det.episode_title == ""
