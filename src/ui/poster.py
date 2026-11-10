"""High-DPI, edge-to-edge artwork with a consistent rounded crop."""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QLabel


def draw_filled_pixmap(painter, area, pixmap, radius=12):
    if pixmap.isNull() or area.isEmpty():
        return
    painter.save()
    clip = QPainterPath(); clip.addRoundedRect(area, radius, radius)
    painter.setClipPath(clip, Qt.IntersectClip)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    source = QRectF(pixmap.rect())
    target_ratio = area.width()/area.height()
    if source.width()/source.height() > target_ratio:
        width = source.height()*target_ratio
        source.setLeft((pixmap.width()-width)/2); source.setWidth(width)
    else:
        height = source.width()/target_ratio
        source.setTop((pixmap.height()-height)/2); source.setHeight(height)
    painter.drawPixmap(area, pixmap, source)
    painter.restore()


class RoundedPoster(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._original = QPixmap()

    def setPixmap(self, pixmap):
        self._original = QPixmap(pixmap)
        self.setText("")
        self.update()

    def pixmap(self):
        return self._original

    def clear(self):
        self._original = QPixmap()
        super().clear()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._original.isNull():
            painter = QPainter(self); painter.setRenderHint(QPainter.Antialiasing)
            draw_filled_pixmap(painter, QRectF(self.rect().adjusted(1,1,-1,-1)), self._original)
