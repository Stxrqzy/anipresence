"""Detector navigation-page guard: home/browse pages that autoplay a trailer
video must not be turned into a detection (there's no anime being identified)."""
from src.browser.detectors import (
    DetectorRegistry,
    PageContext,
    is_navigation_page,
)


def test_home_page_flagged():
    assert is_navigation_page("https://miruro.example/")
    assert is_navigation_page("https://kickassanime.example/home")
    assert is_navigation_page("https://site.example/browse")
    assert is_navigation_page("https://site.example/genre/action")
    assert is_navigation_page("https://site.example/search?q=one+piece")
    assert is_navigation_page("https://site.example/az-list")


def test_watch_pages_not_flagged():
    assert not is_navigation_page("https://miruro.example/watch/113415/jujutsu-kaisen?ep=24")
    assert not is_navigation_page("https://www.crunchyroll.com/watch/GRJDC0XYZ/one-piece")
    assert not is_navigation_page("https://kickassanime.example/ep/123")
    assert not is_navigation_page("https://site.example/play/abc")


def test_registry_rejects_navigation_page():
    reg = DetectorRegistry()
    ctx = PageContext(
        url="https://miruro.example/",
        document_title="Miruro · Watch Free Anime Online · Stream Subbed & Dubbed Anime in HD",
    )
    assert reg.detect(ctx) is None


def test_registry_detects_real_watch_url():
    reg = DetectorRegistry()
    ctx = PageContext(
        url="https://miruro.example/watch/113415/jujutsu-kaisen?ep=24",
        document_title="Jujutsu Kaisen Episode 24",
    )
    det = reg.detect(ctx)
    assert det is not None
    assert det.website == "Miruro"
    assert det.episode == 24