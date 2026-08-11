"""Global-hotkey string parsing/formatting for the Windows RegisterHotKey API.

A hotkey is stored in config as a human-readable spec like ``"Ctrl+Alt+A"`` and
converted to the ``(modifiers, vk)`` pair RegisterHotKey expects. The parsing
lives here (pure Python, no Qt) so it is easily unit-tested.
"""
import re

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008

_MOD_NAMES = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "windows": MOD_WIN,
    "meta": MOD_WIN,
    "super": MOD_WIN,
}
_MOD_LABEL = (
    (MOD_CONTROL, "Ctrl"),
    (MOD_ALT, "Alt"),
    (MOD_SHIFT, "Shift"),
    (MOD_WIN, "Win"),
)

# Printable key name -> Windows virtual-key code. Names are the friendly labels
# used in specs and shown in the UI.
_KEY_CODES = {
    "A": 0x41, "B": 0x42, "C": 0x43, "D": 0x44, "E": 0x45, "F": 0x46,
    "G": 0x47, "H": 0x48, "I": 0x49, "J": 0x4A, "K": 0x4B, "L": 0x4C,
    "M": 0x4D, "N": 0x4E, "O": 0x4F, "P": 0x50, "Q": 0x51, "R": 0x52,
    "S": 0x53, "T": 0x54, "U": 0x55, "V": 0x56, "W": 0x57, "X": 0x58,
    "Y": 0x59, "Z": 0x5A,
    "0": 0x30, "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34,
    "5": 0x35, "6": 0x36, "7": 0x37, "8": 0x38, "9": 0x39,
    "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73, "F5": 0x74,
    "F6": 0x75, "F7": 0x76, "F8": 0x77, "F9": 0x78, "F10": 0x79,
    "F11": 0x7A, "F12": 0x7B,
    "Space": 0x20, "Enter": 0x0D, "Tab": 0x09, "Esc": 0x1B,
    "Up": 0x26, "Down": 0x28, "Left": 0x25, "Right": 0x27,
    "Home": 0x24, "End": 0x23, "PgUp": 0x21, "PgDn": 0x22,
    "Del": 0x2E, "Ins": 0x2D,
}

_CODE_NAMES = {code: name for name, code in _KEY_CODES.items()}
_NAMES_LOWER = {name.lower(): code for name, code in _KEY_CODES.items()}

_RE_PART = re.compile(r"[^+\s]+")


def parse_hotkey(spec):
    """Convert a spec like ``"Ctrl+Alt+A"`` into ``(modifiers, vk)``.

    Returns ``None`` when the spec is empty or contains unknown parts. A key
    with no modifier is rejected (RegisterHotKey would steal that keystroke).
    """
    if not spec or not isinstance(spec, str):
        return None
    modifiers = 0
    key = None
    for part in _RE_PART.findall(spec):
        if part.lower() in _MOD_NAMES:
            modifiers |= _MOD_NAMES[part.lower()]
            continue
        code = _NAMES_LOWER.get(part.lower())
        if code is None:
            return None
        if key is not None:
            return None  # two keys in one spec
        key = code
    if key is None or modifiers == 0:
        return None
    return modifiers, key


def format_hotkey(modifiers, vk):
    """Render ``(modifiers, vk)`` back to a display string like ``"Ctrl+Alt+A"``."""
    parts = [label for mask, label in _MOD_LABEL if modifiers & mask]
    name = _CODE_NAMES.get(vk)
    if name is None:
        return ""
    parts.append(name)
    return "+".join(parts)


def key_name(vk):
    """Friendly name for a virtual-key code ('' for unknowns)."""
    return _CODE_NAMES.get(vk, "")


def key_code(name):
    """Virtual-key code for a friendly name (None for unknowns)."""
    if not name:
        return None
    if len(name) == 1:
        return _KEY_CODES.get(name.upper())
    return _KEY_CODES.get(name)
