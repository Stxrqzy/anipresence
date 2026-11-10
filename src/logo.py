"""Shared app branding for the window, tray, splash and onboarding."""
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QWidget


_LOGO_PATH = Path(__file__).resolve().parents[1] / "assets" / "logo_256.png"
_WORDMARK_PATH = _LOGO_PATH.with_name("wordmark.png")


class OrbitWordmark(QWidget):
    """Scale the transparent wordmark cleanly at any window size or DPI."""

    def __init__(self, parent=None, compact_icon=False):
        super().__init__(parent)
        self._pixmap = QPixmap(str(_WORDMARK_PATH))
        self._compact_icon = QPixmap(str(_LOGO_PATH)) if compact_icon else QPixmap()
        self._original = self._pixmap.toImage()
        self._colors = {}
        self.setAccessibleName("AniPresence")
        self.setObjectName("surfaceContent")

    def sizeHint(self):
        return QSize(200, 64)

    def configure(self, accent, theme):
        """Recolor the purple artwork while retaining its shading and alpha."""
        key = (accent, theme)
        if key not in self._colors:
            tint = QColor(accent)
            image = self._original.scaledToWidth(1000, Qt.SmoothTransformation).convertToFormat(QImage.Format_RGBA8888)
            pixels = image.bits()
            import colorsys
            hue, saturation, _ = tint.getHsvF()[:3]
            for offset in range(0, image.sizeInBytes(), 4):
                if not pixels[offset+3]:
                    continue
                r, g, b = (pixels[offset+i]/255 for i in range(3))
                _, source_saturation, value = colorsys.rgb_to_hsv(r, g, b)
                if theme == "light":
                    value *= .66
                rgb = colorsys.hsv_to_rgb(max(0,hue), source_saturation*saturation, value)
                for i, component in enumerate(rgb):
                    pixels[offset+i] = round(component*255)
            if len(self._colors) >= 8:
                self._colors.clear()
            self._colors[key] = QPixmap.fromImage(image)
        self._pixmap = self._colors[key]
        self.update()

    def paintEvent(self, event):
        pixmap = self._compact_icon if self.width() < 100 and not self._compact_icon.isNull() else self._pixmap
        if pixmap.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        size = pixmap.size().scaled(self.size(), Qt.KeepAspectRatio)
        target = QRectF((self.width()-size.width())/2, (self.height()-size.height())/2,
                        size.width(), size.height())
        painter.drawPixmap(target, pixmap, QRectF(pixmap.rect()))


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
