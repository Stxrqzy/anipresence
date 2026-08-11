"""Fuzzy matching against AniList candidates."""
from src.anime.anilist import Media
from src.anime.matcher import best_match


def media(title="", romaji="", english="", native="", synonyms=None, id=1):
    return Media(id=id, title_romaji=romaji, title_english=english,
                 title_native=native, synonyms=synonyms or [])


def test_empty_list_returns_none():
    assert best_match([], "anything") == (None, 0.0)


def test_exact_match_scores_high():
    entry = media(romaji="Steins;Gate", english="Steins;Gate")
    best, score = best_match([entry], "Steins;Gate")
    assert best is entry
    assert score >= 95


def test_synonym_match():
    entry = media(romaji="Shingeki no Kyojin", synonyms=["Attack on Titan"])
    best, score = best_match([entry], "Attack on Titan")
    assert best is entry
    assert score >= 95


def test_fuzzy_typographical_match():
    entry = media(romaji="Kimi no Na wa")
    best, _ = best_match([entry], "Kimi no Na-Wa!")
    assert best is entry


def test_garbage_search_still_returns_top_entry():
    entry = media(romaji="Sword Art Online")
    best, score = best_match([entry], "zzzz qqqq")
    assert best is entry
    assert score < 60


def test_empty_search_returns_top_entry():
    entry = media(romaji="One Piece")
    best, score = best_match([entry], "   ")
    assert best is entry
    assert score == 0.0


def test_picks_highest_scoring_candidate():
    a = media(id=1, romaji="Fate/stay night")
    b = media(id=2, romaji="Fate/stay night: Unlimited Blade Works")
    best, _ = best_match([a, b], "Fate/stay night")
    assert best is a