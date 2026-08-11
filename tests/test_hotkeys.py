"""Global-hotkey parsing/formatting used by the Settings tab."""
from src.core.hotkeys import (
    MOD_ALT,
    MOD_CONTROL,
    MOD_SHIFT,
    MOD_WIN,
    format_hotkey,
    key_code,
    key_name,
    parse_hotkey,
)


def test_default_spec_parses():
    mods, vk = parse_hotkey("Ctrl+Alt+A")
    assert mods == (MOD_CONTROL | MOD_ALT)
    assert vk == 0x41


def test_modifier_order_does_not_matter():
    assert parse_hotkey("Alt+Ctrl+A") == parse_hotkey("Ctrl+Alt+A")


def test_space_separated_spec():
    mods, vk = parse_hotkey("Ctrl Alt P")
    assert mods == (MOD_CONTROL | MOD_ALT)
    assert vk == 0x50


def test_lowercase_modifiers_accepted():
    mods, vk = parse_hotkey("ctrl+shift+f1")
    assert mods == (MOD_CONTROL | MOD_SHIFT)
    assert vk == 0x70


def test_key_requires_modifier():
    assert parse_hotkey("A") is None


def test_modifier_alone_rejected():
    assert parse_hotkey("Ctrl") is None
    assert parse_hotkey("") is None
    assert parse_hotkey(None) is None


def test_unknown_part_rejected():
    assert parse_hotkey("Ctrl+Alt+Qux") is None


def test_format_roundtrip():
    for spec in ("Ctrl+Alt+A", "Ctrl+Shift+F5", "Alt+P", "Win+Ctrl+Space"):
        parsed = parse_hotkey(spec)
        assert parsed is not None
        assert parse_hotkey(format_hotkey(*parsed)) == parsed


def test_format_unknown_vk_empty():
    assert format_hotkey(MOD_CONTROL, 0xEE) == ""


def test_key_name_and_code():
    assert key_name(0x41) == "A"
    assert key_code("A") == 0x41
    assert key_code("Qux") is None
    assert key_name(0xEE) == ""
