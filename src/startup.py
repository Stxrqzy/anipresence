"""Windows "start with Windows" support via the HKCU Run registry key.

The packaged exe registers itself with `--autostart` so main() knows to launch
minimized-to-tray; in a plain `python main.py` dev run this is a no-op.
"""
import sys

APP_NAME = "AniPresence"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _command():
    if not getattr(sys, "frozen", False):
        return None  # only meaningful for the packaged exe
    return f'"{sys.executable}" --autostart'


def is_enabled():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
        return True
    except OSError:
        return False


def enable_startup():
    command = _command()
    if not command:
        return False
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, command)
        return True
    except OSError:
        return False


def disable_startup():
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, APP_NAME)
        return True
    except OSError:
        return False
