"""Shared app branding for the window, tray, splash and onboarding."""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap


_LOGO_PATH = Path(__file__).resolve().parents[1] / "assets" / "logo_256.png"


def logo_pixmap(size):
    """Load the bundled icon in source runs and PyInstaller installations."""
    pixmap = QPixmap(str(_LOGO_PATH))
    if pixmap.isNull():
        return None
    return pixmap.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def logo_icon(size=64):
    icon = QIcon()
    for resolution in sorted({16, 24, 32, 48, size, 128, 256}):
        pixmap = logo_pixmap(resolution)
        if pixmap is not None:
            icon.addPixmap(pixmap)
    return icon
