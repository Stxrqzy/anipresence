"""Qt bridge between the worker thread and the UI.

The controller runs inside a QThread and must not touch widgets directly. It
emits values through these signals; Qt marshals them onto the main thread where
the window updates itself.
"""
import logging

from PySide6.QtCore import QObject, Signal


class UiBridge(QObject):
    chrome_status = Signal(str, bool)     # (description, connected)
    discord_status = Signal(str, bool)    # (description, connected)
    detection = Signal(dict)              # payload or {"clear": True}
    recent = Signal(list)                 # recently-watched entries
    log = Signal(str)                     # one log line
    stats_imported = Signal(int, str)     # (title count, message) from AniList import
    account_avatar = Signal(str, str)     # (username, avatar URL) — "" URL when none
    library_loaded = Signal(dict)         # {"username", "sections", "message"}
    calendar_loaded = Signal(dict)         # {"username", "season", "entries", "message"}
    share_tunnel = Signal(str)             # public trycloudflare URL ("" = failed)


class QtLogHandler(logging.Handler):
    """Sends log records to the UI log panel via the bridge."""

    def __init__(self, bridge, level=logging.INFO):
        super().__init__(level)
        self.bridge = bridge
        fmt = logging.Formatter("%(levelname).1s %(name)s: %(message)s")
        self.setFormatter(fmt)

    def emit(self, record):
        try:
            self.bridge.log.emit(self.format(record))
        except Exception:  # noqa: BLE001 - never let logging break the app
            pass
