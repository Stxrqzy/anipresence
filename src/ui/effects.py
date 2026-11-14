"""Cached glass backdrop and lightweight navigation feedback."""
from PySide6.QtCore import QEasingCurve, QParallelAnimationGroup, QPropertyAnimation, Qt, Property
from PySide6.QtGui import QColor, QPainter, QRadialGradient, QPixmap
from PySide6.QtWidgets import QLabel, QWidget


class GlassBackground(QWidget):
    def __init__(self):
        super().__init__()
        self.glass = True
        self.theme = "dark"
        self.accent = "#7c5cff"
        self._cache_key = None

    def configure(self, enabled, theme, accent):
        self.glass, self.theme, self.accent = enabled, theme, accent
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.theme != "light":
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor("#000000" if self.theme == "oled" else "#141414"))
            return
        if not self.glass:
            return
        key = (self.size(), self.devicePixelRatioF(), self.theme, self.accent)
        if key == self._cache_key:
            painter = QPainter(self)
            painter.drawPixmap(0, 0, self._background)
            return
        self._cache_key = key
        self._background = QPixmap(self.size()*self.devicePixelRatioF())
        self._background.setDevicePixelRatio(self.devicePixelRatioF())
        painter = QPainter(self._background)
        light = self.theme == "light"
        painter.fillRect(self.rect(), QColor("#edf0f8" if light else "#0e111c"))
        for x, y, radius, color, alpha in (
            (0.12, 0.12, 0.8, self.accent, 32 if light else 58),
            (0.95, 0.85, 0.7, "#54bfd6", 24 if light else 26),
        ):
            tint = QColor(color)
            if not tint.isValid():
                tint = QColor("#7c5cff")
            tint.setAlpha(alpha)
            glow = QRadialGradient(self.width() * x, self.height() * y, max(self.width(), self.height()) * radius)
            glow.setColorAt(0, tint)
            tint.setAlpha(0)
            glow.setColorAt(1, tint)
            painter.fillRect(self.rect(), glow)
        painter.end()
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._background)


class PageTransition(QLabel):
    """A small accent transition, without capturing or compositing whole pages."""
    def __init__(self, parent, accent="#9955ff"):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.accent = QColor(accent)
        self.setGeometry(0, 0, parent.width(), 3)
        self.setStyleSheet("background: transparent; border: none;")
        self._opacity = 1.0
        self.animation = QParallelAnimationGroup(self)
        for target, prop, start, end in (
            (self, b"opacity", 1.0, 0.0),
        ):
            animation = QPropertyAnimation(target, prop, self.animation)
            animation.setDuration(180)
            animation.setStartValue(start)
            animation.setEndValue(end)
            animation.setEasingCurve(QEasingCurve.OutCubic)
            self.animation.addAnimation(animation)
        self.show()
        self.raise_()

    def _set_opacity(self, value):
        self._opacity = value
        self.update()

    opacity = Property(float, lambda self: self._opacity, _set_opacity)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setOpacity(self._opacity)
        painter.fillRect(self.rect(), self.accent)


def glass_qss(theme, accent):
    light = theme == "light"
    top = "rgba(255,255,255,205)" if light else "rgba(43,47,67,175)"
    bottom = "rgba(255,255,255,140)" if light else "rgba(23,27,42,180)"
    edge = "rgba(255,255,255,235)" if light else "rgba(210,220,255,32)"
    sheen = f"qlineargradient(x1:0,y1:0,x2:0.6,y2:1,stop:0 {top},stop:1 {bottom})"
    rules = []
    for root in ("modernRoot", "classicRoot"):
        scope = f"QWidget#{root}"
        panels = ", ".join(f"{scope} QFrame#{name}" for name in
                           ("card", "controls", "sidebar", "preview", "continueStrip", "updateBanner"))
        rules.append(f"""
        {scope} QWidget#surfaceContent, {scope} QLabel, {scope} QCheckBox {{ background: transparent; }}
        {panels} {{ background: {sheen}; border: 1px solid {edge}; border-top-color: {'rgba(255,255,255,255)' if light else 'rgba(225,232,255,58)'}; border-radius: 16px; }}
        {scope} QLabel#cover {{ background: {'rgba(220,225,240,150)' if light else 'rgba(12,16,30,110)'}; border: 1px solid {edge}; }}
        {scope} QLabel#avatar {{ background: {bottom}; }}
        {scope} QLabel#chipOk {{ background: rgba(74,222,128,22); }}
        {scope} QLabel#chipBad {{ background: rgba(248,113,113,22); }}
        {scope} QPushButton#ghost {{ background: {'rgba(255,255,255,65)' if light else 'rgba(160,180,235,10)'}; border: 1px solid {edge}; }}
        {scope} QPushButton#ghost:hover {{ background: {'rgba(255,255,255,185)' if light else 'rgba(160,180,235,30)'}; border-color: {accent}; }}
        {scope} QLineEdit, {scope} QComboBox, {scope} QSpinBox {{ background: {'rgba(255,255,255,145)' if light else 'rgba(10,14,25,100)'}; }}
        {scope} QComboBox QAbstractItemView {{ background: {'#f8f9fd' if light else '#202536'}; }}
        """)
    return "\n".join(rules)
