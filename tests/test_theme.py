"""Theme resolution: fixed themes plus the 'system' branch (registry-driven)."""
from src.theme import resolve_theme


def test_fixed_themes():
    assert resolve_theme("dark") == "dark"
    assert resolve_theme("light") == "light"
    assert resolve_theme("") == "dark"
    assert resolve_theme(None) == "dark"


def test_system_follows_light(monkeypatch):
    monkeypatch.setattr("src.theme._system_theme_is_dark", lambda: False)
    assert resolve_theme("system") == "light"


def test_system_follows_dark(monkeypatch):
    monkeypatch.setattr("src.theme._system_theme_is_dark", lambda: True)
    assert resolve_theme("system") == "dark"