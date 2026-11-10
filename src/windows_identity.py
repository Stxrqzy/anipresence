"""Stable Windows taskbar identity shared with the installer shortcuts."""
import sys

APP_USER_MODEL_ID = "AniPresence.Desktop.Orbit"


def configure_taskbar_identity():
    if sys.platform != "win32":
        return
    import ctypes
    try:
        function = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        function.argtypes = [ctypes.c_wchar_p]
        function.restype = ctypes.c_long
        function(APP_USER_MODEL_ID)
    except (AttributeError, OSError):
        pass
