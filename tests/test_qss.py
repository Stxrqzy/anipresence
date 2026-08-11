"""Theme stylesheet builder: theme selection, accent and layout flags."""
from PySide6.QtGui import QColor

from src.ui.app import (
    ACCENT_PRESETS, _accent_variants, build_theme_qss,
)


def test_dark_and_light_differ():
    dark = build_theme_qss("dark")
    light = build_theme_qss("light")
    assert dark != light


def test_system_resolves_without_error(monkeypatch):
    monkeypatch.setattr("src.ui.app.resolve_theme", lambda theme: "light")
    qss = build_theme_qss("system")
    assert qss == build_theme_qss("light")


def test_accent_substitution():
    accent = "#ff6600"
    qss = build_theme_qss("dark", accent=accent)
    assert accent in qss
    assert "#7c5cff" not in qss


def test_accent_default_is_purple():
    qss = build_theme_qss("dark")
    assert "#7c5cff" in qss


def test_compact_adds_spacing_extra():
    plain = build_theme_qss("dark", compact=False)
    compact = build_theme_qss("dark", compact=True)
    assert len(compact) > len(plain)


def test_mica_drops_opaque_windows():
    plain = build_theme_qss("light", mica=False)
    mica = build_theme_qss("light", mica=True)
    assert "background-color: rgba(0, 0, 0, 0)" in mica
    assert "background-color: #f4f5f7" in plain
    assert "background-color: #f4f5f7" not in mica


def test_invalid_accent_falls_back():
    base, hover, pressed = _accent_variants("not-a-color")
    assert base == "#7c5cff"
    assert QColor(hover).isValid()
    assert QColor(pressed).isValid()


def test_preview_and_ambient_selectors_present_in_both_themes():
    for theme in ("dark", "light"):
        qss = build_theme_qss(theme)
        assert "QLabel#ambient" in qss
        assert "QFrame#preview" in qss
        assert "QLabel#previewTitle" in qss
        assert "QLabel#previewState" in qss
        assert "QLabel#previewTime" in qss
        assert "QLabel#previewButtons" in qss


def test_mica_flag_also_enables_cinematic_transparency():
    # Cinematic mode reuses the mica transparency pass, so the opaque window
    # fills disappear from both themes when the flag is passed.
    for theme in ("dark", "light"):
        qss = build_theme_qss(theme, mica=True)
        assert "background-color: rgba(0, 0, 0, 0)" in qss


def test_hotkey_recording_selector_in_both_themes():
    for theme in ("dark", "light"):
        qss = build_theme_qss(theme)
        assert 'QPushButton#ghost[recording="true"]' in qss


def test_accent_presets_are_valid_hex():
    assert ACCENT_PRESETS, "expected at least one preset"
    for name, hexval in ACCENT_PRESETS:
        assert name
        color = QColor(hexval)
        assert color.isValid(), f"bad preset color {name}: {hexval}"
        assert hexval.lower() == hexval  # findData() matching relies on lowercase


def test_continue_strip_selectors_in_both_themes():
    for theme in ("dark", "light"):
        qss = build_theme_qss(theme)
        assert "QFrame#continueStrip" in qss
        assert "QScrollArea#continueScroller" in qss
        assert "QFrame#continueTile" in qss
        assert "QToolButton#continueTileButton" in qss
        assert "QToolButton#continueTileClose" in qss


def test_selected_tab_uses_accent():
    # The remodeled tab bar highlights the active tab with the accent color.
    for theme in ("dark", "light"):
        qss = build_theme_qss(theme, accent="#ff8800")
        assert "QTabBar::tab:selected" in qss
        assert "#ff8800" in qss