"""Theme resolution: map a stored theme name to an actual dark/light value.

Supports a ``system`` theme that follows Windows' light/dark app setting
(read from the registry), so the UI switches automatically at runtime.
"""
import sys


def _system_theme_is_dark():
    """True when Windows is in dark mode (or a non-Windows fallback)."""
    if not sys.platform.startswith("win"):
        return True
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return value == 0
    except (OSError, TypeError, ValueError):
        return True


def resolve_theme(theme):
    """Return 'dark' or 'light' for a stored theme ('' is treated as dark)."""
    if theme == "system":
        return "dark" if _system_theme_is_dark() else "light"
    return theme if theme in ("light", "oled") else "dark"
