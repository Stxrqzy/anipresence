"""PySide6 main window.

Displays live detection state (cover art, title, episode, progress), connection
status for Chrome and Discord, control toggles, recently-watched history, and a
scrollback log. The worker thread updates it exclusively through signals.
"""
import hashlib
import sys
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import (
    QAbstractNativeEventFilter, QAbstractAnimation, QEasingCurve, QPoint, QPropertyAnimation,
    QRect, QSignalBlocker, QSize, Qt, QTimer, QUrl,
)
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLayout,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSpinBox, QStackedWidget,
    QSystemTrayIcon, QTabBar, QVBoxLayout, QWidget,
)
from PySide6.QtWidgets import QSizePolicy

try:  # QtCharts is optional; the stats chart degrades to a plain label if absent.
    from PySide6.QtCharts import (
        QBarCategoryAxis, QBarSeries, QBarSet, QChart, QChartView, QValueAxis,
    )
    QTCHARTS_OK = True
except Exception:  # noqa: BLE001
    QTCHARTS_OK = False

from .. import __version__
from ..config import APP_DIR, ConfigManager, redact_urls
from ..core.controller import Controller
from ..core.watchlog import format_hours
from ..theme import resolve_theme
from ..ui.bridge import UiBridge
from ..urlnav import increment_episode_url

BROWSER_INDEX = {"chrome": 0, "brave": 1, "edge": 2, "opera": 3}
BROWSER_VALUE = {v: k for k, v in BROWSER_INDEX.items()}
BROWSER_NAMES = {"chrome": "Chrome", "brave": "Brave", "edge": "Edge", "opera": "Opera"}

THEME_INDEX = {"dark": 0, "light": 1, "system": 2}
THEME_VALUE = {v: k for k, v in THEME_INDEX.items()}

BROADCAST_INDEX = {"discord": 0, "vencord": 1, "both": 2}
BROADCAST_VALUE = {v: k for k, v in BROADCAST_INDEX.items()}

DARK_QSS = """
QWidget { background-color: #0e0f14; color: #e7e9ee; font-family: "Figtree", "Segoe UI Variable", "Segoe UI", sans-serif; font-size: 13px; }
QMainWindow { background-color: #0e0f14; }
QLabel#appTitle { font-size: 19px; font-weight: 700; color: #ffffff; }
QLabel#titleLabel { font-size: 26px; font-weight: 650; color: #ffffff; }
QLabel#subLabel { color: #8a91a8; font-size: 12px; }
QLabel#versionLabel { color: #5a6178; font-size: 10px; letter-spacing: 0.5px; }
QLabel#captionLabel { color: #7c5cff; font-size: 13px; font-weight: 600; }
QLabel#sectionLabel { color: #8a91a8; font-size: 11px; font-weight: 700; }
QLabel#statValue { color: #ffffff; font-size: 22px; font-weight: 700; }
QLabel#monogram { background-color: #7c5cff; color: #ffffff; border-radius: 10px; font-size: 16px; font-weight: 700; }
QLabel#avatar { background-color: #3a4057; color: #e7e9ee; border-radius: 17px; font-size: 15px; font-weight: 700; }
QLabel#cover { background-color: rgba(18, 20, 29, 0.62); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 10px; color: #4b5268; }
QTabWidget::pane { border: none; background: transparent; }
QTabBar { background: transparent; }
QTabBar::tab { background-color: rgba(23, 25, 35, 0.45); border: 1px solid rgba(139, 148, 175, 0.14); border-bottom: none; border-top-left-radius: 10px; border-top-right-radius: 10px; padding: 8px 22px; margin-right: 5px; color: #9aa3bd; }
QTabBar::tab:selected { background-color: rgba(23, 25, 35, 0.66); border-color: rgba(139, 148, 175, 0.16); color: #ffffff; }
QTabBar::tab:hover:!selected { background-color: rgba(48, 54, 74, 0.8); color: #e7e9ee; }
QLabel#chipOk { color: #4ade80; background-color: rgba(74, 222, 128, 0.12); padding: 4px 10px; border-radius: 12px; font-weight: 600; font-size: 12px; }
QLabel#chipBad { color: #f87171; background-color: rgba(248, 113, 113, 0.12); padding: 4px 10px; border-radius: 12px; font-weight: 600; font-size: 12px; }
QFrame#card, QFrame#controls { background-color: rgba(23, 25, 35, 0.66); border: 1px solid rgba(139, 148, 175, 0.16); border-radius: 16px; }
QFrame#sep { background-color: rgba(255, 255, 255, 0.08); border: none; max-height: 1px; }
QPushButton { background-color: #7c5cff; color: #ffffff; border: none; border-radius: 8px; padding: 7px 14px; font-weight: 600; }
QPushButton:hover { background-color: #8b6cff; }
QPushButton:pressed { background-color: #6a4ce0; }
QPushButton:disabled { background-color: rgba(38, 42, 55, 0.7); color: #5c6278; }
QPushButton#ghost { background-color: rgba(38, 42, 55, 0.66); color: #cbd0de; border: 1px solid rgba(139, 148, 175, 0.12); }
QPushButton#ghost:hover { background-color: rgba(48, 54, 74, 0.8); }
QCheckBox { spacing: 9px; color: #d6d9e3; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 5px; border: 1.5px solid #3a4057; background-color: rgba(23, 25, 35, 0.8); }
QCheckBox::indicator:hover { border-color: #7c5cff; }
QCheckBox::indicator:checked { background-color: #7c5cff; border-color: #7c5cff; }
QLineEdit, QSpinBox, QComboBox { background-color: rgba(16, 18, 26, 0.6); border: 1px solid rgba(139, 148, 175, 0.16); border-radius: 12px; padding: 6px 10px; color: #e7e9ee; selection-background-color: #7c5cff; selection-color: #ffffff; }
QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border-color: #7c5cff; }
QLineEdit { placeholder-text-color: #5c6278; }
QComboBox { padding-right: 24px; }
QComboBox::drop-down { border: none; background: transparent; width: 22px; border-top-right-radius: 12px; border-bottom-right-radius: 12px; }
QComboBox QAbstractItemView { background-color: rgba(23, 25, 35, 0.92); border: 1px solid rgba(139, 148, 175, 0.16); border-radius: 8px; padding: 4px; color: #e7e9ee; selection-background-color: #30364a; selection-color: #ffffff; }
QProgressBar { background-color: rgba(18, 20, 29, 0.6); border: none; border-radius: 5px; height: 10px; }
QProgressBar::chunk { background-color: #7c5cff; border-radius: 5px; }
QListWidget, QPlainTextEdit { background-color: rgba(18, 20, 29, 0.6); border: 1px solid rgba(139, 148, 175, 0.12); border-radius: 10px; }
QListWidget::item { padding: 6px 8px; border-radius: 6px; color: #cbd0de; }
QListWidget::item:hover { background-color: rgba(28, 31, 44, 0.9); }
QListWidget::item:selected { background-color: rgba(42, 47, 64, 0.9); color: #ffffff; }
QPlainTextEdit { color: #9aa3bd; font-family: Consolas, "Cascadia Mono", monospace; font-size: 12px; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
QScrollBar::handle:vertical { background: #2a2f40; border-radius: 4px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #3a4057; }
QScrollBar:horizontal { background: transparent; height: 8px; margin: 2px; }
QScrollBar::handle:horizontal { background: #2a2f40; border-radius: 4px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QSplitter::handle { background: transparent; }
QSplitter::handle:hover { background: rgba(124, 92, 255, 0.35); }
QToolTip { background-color: rgba(23, 25, 35, 0.92); color: #e7e9ee; border: 1px solid rgba(139, 148, 175, 0.16); padding: 5px 8px; border-radius: 6px; }
"""

LIGHT_QSS = """
QWidget { background-color: #f4f5f7; color: #1c1e26; font-family: "Figtree", "Segoe UI Variable", "Segoe UI", sans-serif; font-size: 13px; }
QMainWindow { background-color: #f4f5f7; }
QLabel#appTitle { font-size: 19px; font-weight: 700; color: #111318; }
QLabel#titleLabel { font-size: 26px; font-weight: 650; color: #111318; }
QLabel#subLabel { color: #6b7280; font-size: 12px; }
QLabel#versionLabel { color: #9aa0b5; font-size: 10px; letter-spacing: 0.5px; }
QLabel#captionLabel { color: #6a49e8; font-size: 13px; font-weight: 700; }
QLabel#sectionLabel { color: #6b7280; font-size: 11px; font-weight: 700; }
QLabel#statValue { color: #111318; font-size: 22px; font-weight: 700; }
QLabel#monogram { background-color: #7c5cff; color: #ffffff; border-radius: 10px; font-size: 16px; font-weight: 700; }
QLabel#avatar { background-color: #c6cbd6; color: #1c1e26; border-radius: 17px; font-size: 15px; font-weight: 700; }
QLabel#cover { background-color: rgba(248, 249, 251, 0.7); border: 1px solid rgba(0, 0, 0, 0.08); border-radius: 10px; color: #9aa0b5; }
QTabWidget::pane { border: none; background: transparent; }
QTabBar { background: transparent; }
QTabBar::tab { background-color: rgba(255, 255, 255, 0.45); border: 1px solid rgba(0, 0, 0, 0.06); border-bottom: none; border-top-left-radius: 10px; border-top-right-radius: 10px; padding: 8px 22px; margin-right: 5px; color: #6b7280; }
QTabBar::tab:selected { background-color: rgba(255, 255, 255, 0.68); border-color: rgba(0, 0, 0, 0.07); color: #111318; }
QTabBar::tab:hover:!selected { background-color: rgba(236, 238, 243, 0.9); color: #111318; }
QLabel#chipOk { color: #15803d; background-color: rgba(22, 163, 74, 0.12); padding: 4px 10px; border-radius: 12px; font-weight: 600; font-size: 12px; }
QLabel#chipBad { color: #b91c1c; background-color: rgba(220, 38, 38, 0.12); padding: 4px 10px; border-radius: 12px; font-weight: 600; font-size: 12px; }
QFrame#card, QFrame#controls { background-color: rgba(255, 255, 255, 0.68); border: 1px solid rgba(0, 0, 0, 0.07); border-radius: 16px; }
QFrame#sep { background-color: rgba(0, 0, 0, 0.08); border: none; max-height: 1px; }
QPushButton { background-color: #7c5cff; color: #ffffff; border: none; border-radius: 8px; padding: 7px 14px; font-weight: 600; }
QPushButton:hover { background-color: #8b6cff; }
QPushButton:pressed { background-color: #6a4ce0; }
QPushButton:disabled { background-color: rgba(236, 238, 243, 0.7); color: #9aa0b5; }
QPushButton#ghost { background-color: rgba(236, 238, 243, 0.72); color: #3b4252; border: 1px solid rgba(0, 0, 0, 0.06); }
QPushButton#ghost:hover { background-color: rgba(226, 229, 236, 0.9); }
QCheckBox { spacing: 9px; color: #374151; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 5px; border: 1.5px solid #c6cbd6; background-color: rgba(255, 255, 255, 0.8); }
QCheckBox::indicator:hover { border-color: #7c5cff; }
QCheckBox::indicator:checked { background-color: #7c5cff; border-color: #7c5cff; }
QLineEdit, QSpinBox, QComboBox { background-color: rgba(255, 255, 255, 0.7); border: 1px solid rgba(0, 0, 0, 0.1); border-radius: 12px; padding: 6px 10px; color: #1c1e26; selection-background-color: #7c5cff; selection-color: #ffffff; }
QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border-color: #7c5cff; }
QLineEdit { placeholder-text-color: #9aa0b5; }
QComboBox { padding-right: 24px; }
QComboBox::drop-down { border: none; background: transparent; width: 22px; border-top-right-radius: 12px; border-bottom-right-radius: 12px; }
QComboBox QAbstractItemView { background-color: rgba(255, 255, 255, 0.96); border: 1px solid rgba(0, 0, 0, 0.1); border-radius: 8px; padding: 4px; color: #1c1e26; selection-background-color: #eceef3; selection-color: #111318; }
QProgressBar { background-color: rgba(236, 238, 243, 0.8); border: none; border-radius: 5px; height: 10px; }
QProgressBar::chunk { background-color: #7c5cff; border-radius: 5px; }
QListWidget, QPlainTextEdit { background-color: rgba(255, 255, 255, 0.7); border: 1px solid rgba(0, 0, 0, 0.07); border-radius: 10px; }
QListWidget::item { padding: 6px 8px; border-radius: 6px; color: #374151; }
QListWidget::item:hover { background-color: rgba(244, 245, 247, 0.9); }
QListWidget::item:selected { background-color: rgba(236, 238, 243, 0.9); color: #111318; }
QPlainTextEdit { color: #4b5563; font-family: Consolas, "Cascadia Mono", monospace; font-size: 12px; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
QScrollBar::handle:vertical { background: #d4d8e0; border-radius: 4px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #c6cbd6; }
QScrollBar:horizontal { background: transparent; height: 8px; margin: 2px; }
QScrollBar::handle:horizontal { background: #d4d8e0; border-radius: 4px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QSplitter::handle { background: transparent; }
QSplitter::handle:hover { background: rgba(124, 92, 255, 0.35); }
QToolTip { background-color: rgba(255, 255, 255, 0.96); color: #1c1e26; border: 1px solid rgba(0, 0, 0, 0.1); padding: 5px 8px; border-radius: 6px; }
"""


# Shared chrome: macOS-style traffic lights + the custom title bar. The colors
# are fixed (mac traffic lights) so they work identically in both themes.
CHROME_QSS = """
QWidget#titlebar { background-color: rgba(255, 255, 255, 0.03); }
QPushButton#winClose { background-color: #ff5f57; color: rgba(0, 0, 0, 0.65); font-size: 10px; font-weight: 800; border: none; border-radius: 7px; }
QPushButton#winClose:hover { background-color: #ff7b74; }
QPushButton#winMin { background-color: #febc2e; color: rgba(0, 0, 0, 0.65); font-size: 10px; font-weight: 800; border: none; border-radius: 7px; }
QPushButton#winMin:hover { background-color: #ffcd4f; }
QPushButton#winMax { background-color: #28c840; color: rgba(0, 0, 0, 0.65); font-size: 10px; font-weight: 800; border: none; border-radius: 7px; }
QPushButton#winMax:hover { background-color: #43d85a; }
QLabel#appLogo { background-color: transparent; border: 1px solid rgba(139, 148, 175, 0.30); border-radius: 10px; }
"""


# Compact-mode overrides appended after the base theme.
COMPACT_EXTRA = """
QPushButton { padding: 4px 9px; }
QCheckBox { spacing: 7px; }
QLabel#appTitle { font-size: 17px; }
QLabel#titleLabel { font-size: 22px; }
QFrame#card, QFrame#controls { border-radius: 10px; }
QLineEdit, QSpinBox, QComboBox { padding: 4px 8px; }
"""


def _accent_variants(accent):
    """Return (base, hover, pressed) hex colors derived from *accent*."""
    base = QColor(accent)
    if not base.isValid():
        base = QColor("#7c5cff")
    hover = base.lighter(112)
    pressed = base.darker(112)
    return base.name(), hover.name(), pressed.name()


def build_theme_qss(theme="dark", accent="", compact=False, mica=False):
    """Return the fully-formed stylesheet for a theme.

    ``accent`` is the user's accent color (defaults to the built-in purple);
    ``compact`` tightens spacing and padding; ``mica`` drops the opaque window
    fills so a Windows Mica/acrylic backdrop shows through (cards stay solid).
    """
    if theme == "system":
        theme = resolve_theme(theme)  # guard old callers holding the raw value
    qss = LIGHT_QSS if theme == "light" else DARK_QSS
    accent = accent or "#7c5cff"
    base, hover, pressed = _accent_variants(accent)
    qss = qss.replace("#7c5cff", base)
    qss = qss.replace("#8b6cff", hover)
    qss = qss.replace("#6a4ce0", pressed)
    qss = qss.replace("#6a49e8", base)
    qss = qss.replace("124, 92, 255", "{}, {}, {}".format(*base_qrgb(accent)))
    qss += CHROME_QSS
    if compact:
        qss += COMPACT_EXTRA
    if mica:
        qss = qss.replace("background-color: #0e0f14", "background-color: rgba(0, 0, 0, 0)")
        qss = qss.replace("background-color: #f4f5f7", "background-color: rgba(0, 0, 0, 0)")
    return qss


def base_qrgb(accent):
    base = QColor(accent)
    if not base.isValid():
        base = QColor("#7c5cff")
    return base.red(), base.green(), base.blue()


def _version_tuple(text):
    """Parse '1.2.3' into (1, 2, 3) for comparison, or None if malformed."""
    try:
        return tuple(int(p) for p in str(text or "").strip().split(".")[:3])
    except (ValueError, AttributeError):
        return None


def app_icon(size=64, accent="#7c5cff"):
    """Window/tray icon: the app logo, falling back to the "AP" monogram."""
    try:
        from ..logo import logo_pixmap
        pm = logo_pixmap(size)
        if pm is not None:
            return QIcon(pm)
    except Exception:  # noqa: BLE001 - never let the icon break the app
        pass
    base, hover, pressed = _accent_variants(accent)
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(base))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(0, 0, size, size, round(size * 0.22), round(size * 0.22))
    p.setPen(QPen(QColor("#ffffff")))
    font = p.font()
    font.setPixelSize(int(size * 0.42))
    font.setBold(True)
    p.setFont(font)
    p.drawText(pm.rect(), Qt.AlignCenter, "AP")
    p.end()
    return QIcon(pm)


class _FlowLayout(QLayout):
    """Left-to-right layout that wraps widgets onto extra lines when narrow."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = []

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_flow(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_flow(rect, apply=True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize(0, 0)
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        l, t, r, b = self.getContentsMargins()
        return size + QSize(l + r, t + b)

    def _do_flow(self, rect, apply=True):
        l, t, r, b = self.getContentsMargins()
        effective = rect.adjusted(l, t, -r, -b)
        spacing = self.spacing()
        if spacing < 0:
            spacing = 6
        x, y = effective.x(), effective.y()
        line_height = 0
        hint = None
        for item in self._items:
            w = item.sizeHint().width()
            h = item.sizeHint().height()
            next_x = x + w + spacing
            if next_x - effective.right() > 0 and line_height > 0:
                x = effective.x()
                y += line_height + spacing
                line_height = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), QSize(w, h)))
            x += w + spacing
            line_height = max(line_height, h)
        return y + line_height - effective.y() + b


class _TrafficButton(QPushButton):
    """macOS-style traffic light: a colored dot that shows its glyph on hover."""

    def __init__(self, glyph, object_name, parent=None):
        super().__init__("", parent)
        self.setObjectName(object_name)
        self.setFixedSize(14, 14)
        self.setCursor(Qt.ArrowCursor)
        self._glyph = glyph
        self._alt_glyph = None  # alternate glyph while the window is maximized

    def set_alt_glyph(self, glyph):
        self._alt_glyph = glyph

    def _current_glyph(self):
        window = self.window()
        if self._alt_glyph and window is not self and window.isMaximized():
            return self._alt_glyph
        return self._glyph

    def enterEvent(self, event):  # noqa: N802 (Qt naming)
        self.setText(self._current_glyph())
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802 (Qt naming)
        self.setText("")
        super().leaveEvent(event)


class _ChromeTitleBar(QWidget):
    """The draggable macOS-style title bar that also hosts the traffic lights."""

    def mousePressEvent(self, event):  # noqa: N802 (Qt naming)
        if event.button() == Qt.LeftButton:
            window = self.window()
            handle = window.windowHandle()
            try:
                handle.startSystemMove()
            except AttributeError:
                window._title_drag = event.globalPosition().toPoint() - window.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802 (Qt naming)
        window = self.window()
        drag = getattr(window, "_title_drag", None)
        if drag is not None and event.buttons() & Qt.LeftButton:
            window.move(event.globalPosition().toPoint() - drag)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802 (Qt naming)
        self.window()._title_drag = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):  # noqa: N802 (Qt naming)
        if event.button() == Qt.LeftButton and hasattr(self.window(), "_toggle_max"):
            self.window()._toggle_max()
        super().mouseDoubleClickEvent(event)


class _Spinner(QWidget):
    """A lightweight rotating arc used as a loading indicator."""

    def __init__(self, size=54):
        super().__init__()
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._angle = 0
        self._color = QColor("#7c5cff")
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def set_color(self, color):
        self._color = QColor(color)
        self.update()

    def _tick(self):
        self._angle = (self._angle + 8) % 360
        self.update()

    def showEvent(self, event):  # noqa: N802 (Qt naming)
        super().showEvent(event)
        self._timer.start()

    def hideEvent(self, event):  # noqa: N802 (Qt naming)
        super().hideEvent(event)
        self._timer.stop()

    def paintEvent(self, event):  # noqa: N802 (Qt naming)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(self._color, 4)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawArc(self.rect().adjusted(6, 6, -6, -6), self._angle * 16, 2790)


class _EdgeResizeFilter(QAbstractNativeEventFilter):
    """Makes the borderless window resizable via native WM_NCHITTEST.

    Windows does all the geometry work when a WM_NCHITTEST returns one of the
    HTLEFT/HTTOP/HTRIGHT/HTBOTTOM (and corner) codes, which keeps resize smooth
    and even gives edge-snap behavior.
    """

    def __init__(self, window_getter, margin=8):
        super().__init__()
        self._window = window_getter
        self._margin = margin

    def nativeEventFilter(self, event_type, message):
        if event_type != b"windows_generic_MSG":
            return False, 0
        try:
            import ctypes
            from ctypes import wintypes

            class POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

            class MSG(ctypes.Structure):
                _fields_ = [
                    ("hwnd", wintypes.HWND),
                    ("message", wintypes.UINT),
                    ("wParam", wintypes.WPARAM),
                    ("lParam", wintypes.LPARAM),
                    ("time", wintypes.DWORD),
                    ("pt", POINT),
                ]

            msg = MSG.from_address(int(message))
            if msg.message == 0x0312:  # WM_HOTKEY (global Ctrl+Alt+A)
                window = self._window()
                if window is not None:
                    window._toggle_visible()
                return True, 0
            if msg.message != 0x84:  # WM_NCHITTEST
                return False, 0
            window = self._window()
            if window is None or int(msg.hwnd) != int(window.winId()):
                return False, 0
            if window.isMaximized():
                return False, 0
            # WM_NCHITTEST coordinates arrive in *physical* pixels, while Qt's
            # geometry()/rect() are DPI-scaled (logical) pixels. Comparing them
            # directly made the resize border ~(scale factor) wide. Use the
            # cursor's logical position instead so the border is exactly N px.
            from PySide6.QtGui import QCursor
            pos = window.mapFromGlobal(QCursor.pos())
            w = window.width()
            h = window.height()
            m = self._margin
            left = pos.x() <= m
            right = pos.x() >= w - 1 - m
            top = pos.y() <= m
            bottom = pos.y() >= h - 1 - m
            if top and left:
                return True, 0xD  # HTTOPLEFT = 13
            if top and right:
                return True, 0xE  # HTTOPRIGHT = 14
            if bottom and left:
                return True, 0x10  # HTBOTTOMLEFT = 16
            if bottom and right:
                return True, 0x11  # HTBOTTOMRIGHT = 17
            if left:
                return True, 0xA  # HTLEFT = 10
            if right:
                return True, 0xB  # HTRIGHT = 11
            if top:
                return True, 0xC  # HTTOP = 12
            if bottom:
                return True, 0xF  # HTBOTTOM = 15
        except Exception:  # noqa: BLE001 - never interfere with the app
            return False, 0
        return False, 0


class MainWindow(QMainWindow):
    def __init__(self, cfg: ConfigManager, bridge: UiBridge, controller: Controller):
        super().__init__()
        self.cfg = cfg
        self.bridge = bridge
        self.controller = controller

        self._current_payload = None
        self._cover_url = ""
        self._cover_requested = False
        self._cover_pixmap = None
        self._left_pane = None
        self._splitter = None
        self._tray = None
        self._quitting = False
        self._prev_chrome_ok = None
        self._prev_discord_ok = None
        self._last_chrome_status = ""
        self._nam = QNetworkAccessManager(self)
        self._nam.finished.connect(self._on_cover_reply)
        self._cover_cache_dir = APP_DIR / "covers"
        self._recent_icon_pending = {}   # cover-url -> QListWidgetItem waiting for its icon
        self._recent_icon_fetching = set()  # cover-urls with a download in flight
        self._stats_icon_pending = {}    # cover-url -> QListWidgetItem (stats list)
        self._stats_icon_fetching = set()
        self._section_icon_pending = {}  # cover-url -> QListWidgetItem (AniList sections)

        self._title_drag = None
        # Animated minimize/restore state (see _animated_minimize/_animated_restore).
        self._geom_anim = None
        self._fade_anim = None
        self._minimize_target = None
        self._restore_geometry = None
        self._restore_maximized = False
        self._normal_geometry = None
        # Header elements hidden at narrow widths (see _update_header_for_width).
        self._header_hid_chips = False
        self._header_hid_sub = False
        # Borderless window: we draw our own macOS-style title bar + traffic lights.
        # WindowMinimizeButtonHint keeps WS_MINIMIZEBOX so the Windows taskbar
        # button can still minimize the window (click taskbar icon to toggle).
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowMinimizeButtonHint)

        self.setWindowTitle(f"AniPresence {__version__}")
        self.resize(960, 780)
        self.setMinimumSize(600, 430)
        self.setWindowIcon(app_icon(64, cfg.data.accent))
        self._applied_theme = resolve_theme(cfg.data.theme)
        self._start_maximized = False
        self._system_refresh = QTimer(self)
        self._system_refresh.setInterval(10000)
        self._system_refresh.timeout.connect(self._maybe_resync_system_theme)
        self._build_ui()
        self._connect_signals()
        self._load_toggles_from_config()
        self._build_tray()
        self.show_placeholder()
        self._apply_backdrop()
        self._install_resize_filter()
        self._restore_window_state()
        self._system_refresh.start()

    # ----------------------------------------------------------------- tray
    def _build_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self._tray = QSystemTrayIcon(app_icon(64, self.cfg.data.accent), self)
        menu = QMenu()
        show_act = menu.addAction("Show / Hide window")
        show_act.triggered.connect(self._toggle_visible)
        menu.addSeparator()
        self._tray_act_site = menu.addAction("Open current site")
        self._tray_act_site.setEnabled(False)
        self._tray_act_site.triggered.connect(self._open_site)
        self._tray_act_pwa = menu.addAction("Launch current as PWA")
        self._tray_act_pwa.setEnabled(False)
        self._tray_act_pwa.triggered.connect(self._open_pwa)
        menu.addSeparator()
        clear_act = menu.addAction("Clear presence")
        clear_act.triggered.connect(self.controller.clear_rpc)
        quit_act = menu.addAction("Quit AniPresence")
        quit_act.triggered.connect(self._quit_app)
        self._tray.setContextMenu(menu)
        self._tray.setToolTip("AniPresence")
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick, QSystemTrayIcon.MiddleClick):
            self._toggle_visible()

    def _toggle_visible(self):
        if self.isVisible():
            self.hide()
        else:
            self._animated_restore()

    def _notify(self, title, message):
        if self._tray and self._tray.isVisible():
            self._tray.showMessage(title, message, QSystemTrayIcon.Information, 2500)

    def _quit_app(self):
        self._quitting = True
        self.close()

    def _toggle_max(self):
        """Animate the window expanding to full-screen or back to normal."""
        if self.isMaximized():
            end = self._normal_geometry or self.geometry()
            self._run_geom_anim(
                self.geometry(), end,
                duration=300, easing=QEasingCurve.OutCubic, on_finish=self._finish_restore_max_anim,
            )
        else:
            self._normal_geometry = self.geometry()
            target = self.screen().availableGeometry()
            self._run_geom_anim(
                self.geometry(), target,
                duration=300, easing=QEasingCurve.OutCubic, on_finish=self._finish_max_anim,
            )

    def _finish_max_anim(self):
        self.showMaximized()
        self._geom_anim = None

    def _finish_restore_max_anim(self):
        self.showNormal()
        self._geom_anim = None

    def _run_geom_anim(self, start, end, duration=260, easing=None, on_finish=None):
        """Animate the window from *start* to *end* geometry (smooth + snappy)."""
        anim = QPropertyAnimation(self, b"geometry", self)
        anim.setDuration(duration)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.setEasingCurve(easing if easing is not None else QEasingCurve.InOutCubic)
        if on_finish is not None:
            anim.finished.connect(on_finish)
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        self._geom_anim = anim  # keep a reference so GC can't kill it mid-flight

    def _animated_minimize(self):
        """Shrink the window down toward the taskbar, then minimize it there."""
        self._restore_maximized = self.isMaximized()
        if self._restore_maximized:
            self.showNormal()  # animate from the restored size
        self._restore_geometry = self.geometry()
        avail = self.screen().availableGeometry()
        w = max(60, self.width() // 3)
        h = max(28, self.height() // 6)
        end = QRect(avail.center().x() - w // 2, avail.bottom() - h, w, h)
        self._minimize_target = end
        self._run_geom_anim(
            self.geometry(), end,
            duration=300, easing=QEasingCurve.OutCubic, on_finish=self._finish_minimize_anim,
        )

    def _finish_minimize_anim(self):
        self.showMinimized()  # go to the Windows taskbar, not the tray
        self._geom_anim = None

    def _animated_restore(self):
        """Expand back from the tray-minimized size to the last window geometry."""
        if self._minimize_target is None:
            self.show()
            self.raise_()
            self.activateWindow()
            return
        self.show()
        self.raise_()
        self.activateWindow()
        end = self._restore_geometry or self.geometry()
        self._minimize_target = None
        self._run_geom_anim(
            self.geometry(), end,
            duration=320, easing=QEasingCurve.OutCubic, on_finish=self._finish_restore_anim,
        )

    def _finish_restore_anim(self):
        if self._restore_maximized:
            self.showMaximized()
        self._restore_maximized = False
        self._geom_anim = None

    def changeEvent(self, event):  # noqa: N802 (Qt naming)
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.WindowStateChange:
            btn = self.win_max_btn
            if btn.underMouse():
                btn.setText(btn._current_glyph())
            # Restored from the taskbar: bounce back to the pre-minimize geometry.
            if (
                self._minimize_target is not None
                and not self.isMinimized()
                and not self.isMaximized()
            ):
                start = self.geometry()
                end = self._restore_geometry or start
                self._minimize_target = None
                self._run_geom_anim(
                    start, end,
                    duration=320, easing=QEasingCurve.OutCubic, on_finish=self._finish_restore_anim,
                )
        super().changeEvent(event)

    def _update_header_for_width(self):
        """Tuck header elements away on narrow windows so nothing gets clipped."""
        w = self.width()
        if w < 820 and not self._header_hid_chips:
            self.chrome_chip.hide()
            self.discord_chip.hide()
            self._header_hid_chips = True
        elif w >= 820 and self._header_hid_chips:
            self.chrome_chip.show()
            self.discord_chip.show()
            self._header_hid_chips = False
        if w < 700 and not self._header_hid_sub:
            self._subtitle_label.hide()
            self._version_label.hide()
            self._header_hid_sub = True
        elif w >= 700 and self._header_hid_sub:
            self._subtitle_label.show()
            self._version_label.show()
            self._header_hid_sub = False

    def _install_resize_filter(self):
        """Enable native edge-resize for the borderless window (Windows only)."""
        if not sys.platform.startswith("win"):
            return
        self._resize_filter = _EdgeResizeFilter(lambda: self)
        QApplication.instance().installNativeEventFilter(self._resize_filter)
        self._register_hotkey()

    _HOTKEY_ID = 0xAF01          # arbitrary app-private id
    _HOTKEY_MODS = 0x0001 | 0x0002  # MOD_ALT | MOD_CONTROL
    _HOTKEY_VK = 0x41             # 'A' -> Ctrl+Alt+A

    def _register_hotkey(self):
        """Global Ctrl+Alt+A toggles the window from anywhere (Windows only)."""
        self._hotkey_registered = False
        try:
            import ctypes
            self._hotkey_registered = bool(
                ctypes.windll.user32.RegisterHotKey(
                    int(self.winId()), self._HOTKEY_ID,
                    self._HOTKEY_MODS, self._HOTKEY_VK,
                )
            )
        except (OSError, AttributeError, ValueError):
            self._hotkey_registered = False

    def _unregister_hotkey(self):
        if not getattr(self, "_hotkey_registered", False):
            return
        try:
            import ctypes
            ctypes.windll.user32.UnregisterHotKey(int(self.winId()), self._HOTKEY_ID)
        except (OSError, AttributeError, ValueError):
            pass
        self._hotkey_registered = False

    def _save_window_state(self):
        """Persist size/position, maximized state and the active tab."""
        if not self.isMinimized():
            geom = self.normalGeometry() if self.isMaximized() else self.geometry()
            if geom.isEmpty():
                geom = self.geometry()
            self.cfg.set(
                window_geometry=f"{geom.x()},{geom.y()},{geom.width()},{geom.height()}",
                window_maximized=self.isMaximized(),
                current_tab=self.tabbar.currentIndex(),
            )

    def _restore_window_state(self):
        """Bring the window back where the user left it on the last run."""
        data = self.cfg.data
        if data.window_geometry:
            try:
                x, y, w, h = (int(p) for p in data.window_geometry.split(","))
            except (ValueError, AttributeError, TypeError):
                x = y = w = h = 0
            if w > 100 and h > 100:
                rect = QRect(x, y, w, h)
                on_screen = any(
                    screen.availableGeometry().intersects(rect)
                    for screen in QApplication.screens()
                )
                if on_screen:
                    self.setGeometry(rect)
        self._start_maximized = bool(data.window_maximized)
        tab = data.current_tab
        if 0 <= tab < self.tabbar.count():
            self.tabbar.setCurrentIndex(tab)

    # ------------------------------------------------------------------ UI
    def _sep(self):
        line = QFrame()
        line.setObjectName("sep")
        line.setFixedHeight(1)
        return line

    def _build_ui(self):
        compact = bool(self.cfg.data.compact)
        self._gap = 8 if compact else 12
        self._pad = 10 if compact else 15
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.setCentralWidget(central)

        # Title bar: mac-style traffic lights + draggable header.
        self.title_bar = _ChromeTitleBar()
        self.title_bar.setObjectName("titlebar")
        header = QHBoxLayout(self.title_bar)
        header.setContentsMargins(16, 8, 16, 2)
        header.setSpacing(12)

        self.win_close_btn = _TrafficButton("\u2715", "winClose")
        self.win_close_btn.setToolTip("Close to tray")
        self.win_min_btn = _TrafficButton("\u2212", "winMin")
        self.win_min_btn.setToolTip("Minimize")
        self.win_max_btn = _TrafficButton("+", "winMax")
        self.win_max_btn.setToolTip("Maximize / restore")
        self.win_max_btn.set_alt_glyph("\u25fb")  # "restore" square while maximized
        # Right-edge cluster: yellow (min), green (max), red (close) left to right.
        for btn in (self.win_min_btn, self.win_max_btn, self.win_close_btn):
            header.addWidget(btn)

        logo_label = QLabel()
        logo_label.setObjectName("appLogo")
        logo_label.setFixedSize(38, 38)
        logo_label.setAlignment(Qt.AlignCenter)
        logo_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        try:
            from ..logo import logo_pixmap
            pm = logo_pixmap(38)
            if pm is not None:
                logo_label.setPixmap(pm)
            else:
                logo_label.setText("AP")
        except Exception:  # noqa: BLE001
            logo_label.setText("AP")
        header.addWidget(logo_label)

        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title = QLabel("AniPresence")
        title.setObjectName("appTitle")
        title.setAttribute(Qt.WA_TransparentForMouseEvents)
        title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        subtitle = QLabel("Chrome → AniList → Discord Rich Presence")
        subtitle.setObjectName("subLabel")
        subtitle.setAttribute(Qt.WA_TransparentForMouseEvents)
        subtitle.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._subtitle_label = subtitle
        self._version_label = QLabel(f"Version {__version__} · click for info")
        self._version_label.setObjectName("versionLabel")
        self._version_label.setCursor(Qt.PointingHandCursor)
        self._version_label.setToolTip("About AniPresence")
        self._version_label.mousePressEvent = lambda _event: self._on_about()
        self._version_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        title_col.addWidget(title)
        title_col.addWidget(subtitle)
        title_col.addWidget(self._version_label)
        header.addLayout(title_col)
        header.addStretch(1)

        self.chrome_chip = QLabel(
            f"{BROWSER_NAMES.get(self.cfg.data.browser, 'Chrome')} · …"
        )
        self.chrome_chip.setObjectName("chipBad")
        self.chrome_chip.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.discord_chip = QLabel("Discord: ...")
        self.discord_chip.setObjectName("chipBad")
        self.discord_chip.setAttribute(Qt.WA_TransparentForMouseEvents)
        header.addWidget(self.chrome_chip)
        header.addWidget(self.discord_chip)

        header.addSpacing(8)
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["Dark", "Light", "System"])
        self.theme_combo.setToolTip("Interface theme. 'System' follows Windows light/dark mode.")
        self.theme_combo.setFixedWidth(88)
        header.addWidget(self.theme_combo)
        self.accent_btn = QPushButton("Accent")
        self.accent_btn.setObjectName("ghost")
        self.accent_btn.setToolTip("Change the accent color")
        header.addWidget(self.accent_btn)
        header.addSpacing(6)
        for btn in (self.win_min_btn, self.win_max_btn, self.win_close_btn):
            header.addWidget(btn)
        # Keep the title bar pinned above the scrollable body.
        outer.insertWidget(0, self.title_bar)

        # Chrome-style top-level tabs: the main dashboard, recently-watched and
        # the log each get their own tab; switching tabs swaps the whole page.
        self.tabbar = QTabBar()
        self.tabbar.setDrawBase(False)
        tab_row = QHBoxLayout()
        tab_row.setContentsMargins(0, 6, 0, 0)
        tab_row.setSpacing(0)
        tab_row.addStretch(1)
        tab_row.addWidget(self.tabbar)
        tab_row.addStretch(1)
        outer.addLayout(tab_row)
        self.stack = QStackedWidget()

        # "Main" tab: the dashboard scrolls independently so every control stays
        # reachable on short windows, and is capped to a comfortable centered width.
        m = 14 if compact else 16
        main_scroll = QScrollArea()
        main_scroll.setObjectName("scroller")
        main_scroll.setWidgetResizable(True)
        main_scroll.setFrameShape(QFrame.NoFrame)
        main_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        main_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        host = QWidget()
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(m, (6 if not compact else 4), m, m)
        host_layout.setSpacing(self._gap)
        dashboard = QWidget()
        host_layout.addWidget(dashboard, 1)
        main_scroll.setWidget(host)
        self.stack.addWidget(main_scroll)

        recent_page = QWidget()
        recent_layout = QVBoxLayout(recent_page)
        recent_layout.setContentsMargins(m, (6 if not compact else 4), m, m)
        recent_layout.setSpacing(self._gap)
        recent_header = QHBoxLayout()
        recent_header.setSpacing(8)
        recent_header.addWidget(self._field_label("Recently watched"))
        recent_header.addStretch(1)
        clear_history_btn = QPushButton("Clear history")
        clear_history_btn.setObjectName("ghost")
        clear_history_btn.setToolTip("Remove every entry from the watch history.")
        clear_history_btn.clicked.connect(self._on_clear_history)
        recent_header.addWidget(clear_history_btn)
        recent_layout.addLayout(recent_header)
        self.recent_list = QListWidget()
        self.recent_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.recent_list.setIconSize(QSize(38, 53))
        recent_layout.addWidget(self.recent_list, 1)
        self.stack.addWidget(recent_page)

        self._build_stats_page(m, compact)
        self.stack.addWidget(self._stats_page)

        log_page = QWidget()
        log_layout = QVBoxLayout(log_page)
        log_layout.setContentsMargins(m, (6 if not compact else 4), m, m)
        log_layout.setSpacing(self._gap)
        log_header = QHBoxLayout()
        log_header.setSpacing(8)
        log_header.addWidget(self._field_label("Activity log"))
        log_header.addStretch(1)
        clear_log_btn = QPushButton("Clear log")
        clear_log_btn.setObjectName("ghost")
        clear_log_btn.setToolTip("Clear the on-screen activity log.")
        clear_log_btn.clicked.connect(self._on_clear_log)
        log_header.addWidget(clear_log_btn)
        log_layout.addLayout(log_header)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        log_layout.addWidget(self.log_view, 1)
        self.stack.addWidget(log_page)

        self.tabbar.addTab("Main")
        self.tabbar.addTab("Recently Watched")
        self.tabbar.addTab("Stats")
        self.tabbar.addTab("Log")
        self._stats_tab_index = self.tabbar.count() - 2
        self.tabbar.currentChanged.connect(self._on_tab_changed)
        outer.addWidget(self.stack, 1)

        left = QHBoxLayout()          # dashboard row: card | controls
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(self._gap)
        dashboard.setLayout(left)

        card = QFrame()
        card.setObjectName("card")
        card.setMinimumWidth(420)
        self._left_pane = card
        card_layout = QHBoxLayout(card)
        card_layout.setContentsMargins(self._pad, self._pad, self._pad, self._pad)
        card_layout.setSpacing(18)

        self.cover_label = QLabel()
        self.cover_label.setObjectName("cover")
        self.cover_label.setFixedSize(150, 210)
        self.cover_label.setAlignment(Qt.AlignCenter)
        card_layout.addWidget(self.cover_label, 0, Qt.AlignTop)
        self._cover_spinner = _Spinner()
        self._cover_spinner.setParent(self.cover_label)
        self._cover_spinner.set_color(self.cfg.data.accent)
        self._cover_spinner.hide()
        self._place_cover_spinner()

        info = QVBoxLayout()
        info.setSpacing(7)
        self.episode_label = QLabel("")
        self.episode_label.setObjectName("captionLabel")
        info.addWidget(self.episode_label)

        self.anime_title = QLabel("No anime detected")
        self.anime_title.setObjectName("titleLabel")
        self.anime_title.setWordWrap(True)
        info.addWidget(self.anime_title)

        self.match_label = QLabel("")
        self.match_label.setObjectName("subLabel")
        self.match_label.setWordWrap(True)
        info.addWidget(self.match_label)

        info.addSpacing(6)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        info.addWidget(self.progress)

        time_row = QHBoxLayout()
        self.time_cur = QLabel("00:00")
        self.time_total = QLabel("00:00")
        self.time_cur.setObjectName("subLabel")
        self.time_total.setObjectName("subLabel")
        time_row.addWidget(self.time_cur)
        time_row.addStretch(1)
        time_row.addWidget(self.time_total)
        info.addLayout(time_row)

        button_row = _FlowLayout()
        button_row.setSpacing(8)
        self.open_site_btn = QPushButton("Open Site")
        self.open_site_btn.setObjectName("ghost")
        self.open_site_btn.setEnabled(False)
        self.open_pwa_btn = QPushButton("Launch as PWA")
        self.open_pwa_btn.setObjectName("ghost")
        self.open_pwa_btn.setToolTip(
            "Reopen this anime in a standalone app-style window of your browser."
        )
        self.open_pwa_btn.setEnabled(False)
        self.open_anilist_btn = QPushButton("Open AniList")
        self.open_anilist_btn.setObjectName("ghost")
        self.open_anilist_btn.setToolTip("Open this anime's AniList page")
        self.open_anilist_btn.setEnabled(False)
        self.fix_match_btn = QPushButton("Wrong match?")
        self.fix_match_btn.setObjectName("ghost")
        self.fix_match_btn.setToolTip(
            "Tell AniPresence the correct title/episode so it stops "
            "misidentifying this page (remembered for next time)."
        )
        self.fix_match_btn.setEnabled(False)
        self.next_ep_btn = QPushButton("Next episode")
        self.next_ep_btn.setObjectName("ghost")
        self.next_ep_btn.setToolTip("Open the next episode of this anime in your browser.")
        self.next_ep_btn.setEnabled(False)
        self.copy_url_btn = QPushButton("Copy URL")
        self.copy_url_btn.setObjectName("ghost")
        self.copy_url_btn.setToolTip("Copy the current page URL to the clipboard.")
        self.copy_url_btn.setEnabled(False)
        button_row.addWidget(self.open_site_btn)
        button_row.addWidget(self.open_pwa_btn)
        button_row.addWidget(self.open_anilist_btn)
        button_row.addWidget(self.fix_match_btn)
        button_row.addWidget(self.next_ep_btn)
        button_row.addWidget(self.copy_url_btn)
        info.addLayout(button_row)
        info.addStretch(1)
        card_layout.addLayout(info, 1)
        left.addWidget(card, 5)

        # Controls
        controls = QFrame()
        controls.setObjectName("controls")
        c_layout = QVBoxLayout(controls)
        c_layout.setContentsMargins(self._pad, (12 if not self.cfg.data.compact else 8), self._pad, self._pad)
        c_layout.setSpacing(10)
        c_layout.addWidget(self._field_label("CONTROLS"))

        self.rpc_check = QCheckBox("Enable Discord RPC")
        self.detection_check = QCheckBox("Enable auto detection")
        self.auto_chrome_check = QCheckBox("Auto-launch browser")
        self.tray_check = QCheckBox("Minimize to tray on close")
        self.startup_check = QCheckBox("Start with Windows")
        self.privacy_check = QCheckBox("Privacy mode (hide URLs in log)")
        self.compact_check = QCheckBox("Compact layout")
        self.idle_check = QCheckBox("Idle presence when nothing playing")
        self.idle_check.setToolTip("Show a 'Browsing anime' presence instead of clearing it when no video is detected.")
        self.mica_check = QCheckBox("Mica backdrop (Windows 11)")
        self.mica_check.setToolTip("Draw the Mica material behind the window (applies on next start; falls back to acrylic).")
        self.hide_adult_check = QCheckBox("Hide 18+/ecchi from Discord status")
        self.hide_adult_check.setToolTip(
            "Don't broadcast animes that are 18+ or ecchi/NSFW-adjacent to Discord. "
            "They still appear in the app's own history and stats."
        )
        # Two-column checkbox grid keeps the controls section short so the whole
        # dashboard (and the right column next to it) doesn't sprawl.
        boxes = [
            self.rpc_check, self.detection_check, self.auto_chrome_check,
            self.tray_check, self.startup_check, self.privacy_check,
            self.compact_check, self.idle_check, self.mica_check,
            self.hide_adult_check,
        ]
        grid = QGridLayout()
        grid.setSpacing(8)
        for i, cb in enumerate(boxes):
            grid.addWidget(cb, i // 2, i % 2)
        c_layout.addLayout(grid)

        c_layout.addWidget(self._sep())

        c_layout.addWidget(self._field_label("ANILIST ACCOUNT"))
        account_row = QHBoxLayout()
        account_row.setSpacing(8)
        self.account_avatar = QLabel()
        self.account_avatar.setObjectName("avatar")
        self.account_avatar.setFixedSize(34, 34)
        self.account_avatar.setAlignment(Qt.AlignCenter)
        self.account_avatar.setToolTip("Active AniList account")
        self._account_avatar_url = ""
        self._avatar_pending_reply = {}  # avatar-url -> QLabel waiting for its image
        self._avatar_fetching = set()
        account_row.addWidget(self.account_avatar)
        self.anilist_combo = QComboBox()
        self.anilist_combo.setToolTip(
            "Switch which account the Stats tab loads. Each account keeps its "
            "own watch history in a separate save file."
        )
        account_row.addWidget(self.anilist_combo, 1)
        add_acct_btn = QPushButton("Add")
        add_acct_btn.setObjectName("ghost")
        add_acct_btn.setToolTip("Add an AniList account and switch to it")
        add_acct_btn.clicked.connect(self._on_add_anilist_account)
        account_row.addWidget(add_acct_btn)
        remove_acct_btn = QPushButton("Remove")
        remove_acct_btn.setObjectName("ghost")
        remove_acct_btn.setToolTip("Remove the selected account (its save file is kept)")
        remove_acct_btn.clicked.connect(self._on_remove_anilist_account)
        account_row.addWidget(remove_acct_btn)
        c_layout.addLayout(account_row)
        account_hint = QLabel(
            "Each account's watch history loads from its own save file on switch."
        )
        account_hint.setObjectName("subLabel")
        c_layout.addWidget(account_hint)
        self._populate_anilist_accounts()

        c_layout.addWidget(self._sep())

        c_layout.addWidget(self._field_label("DISCORD APPLICATION ID"))
        id_row = QHBoxLayout()
        id_row.setSpacing(8)
        self.app_id_edit = QLineEdit()
        self.app_id_edit.setPlaceholderText("Paste your Application ID")
        self.app_id_edit.setText(self.cfg.data.discord_application_id)
        id_row.addWidget(self.app_id_edit, 1)
        get_id_btn = QPushButton("Get ID")
        get_id_btn.setObjectName("ghost")
        get_id_btn.setToolTip("Open the Discord Developer Portal")
        get_id_btn.clicked.connect(
            lambda: webbrowser.open("https://discord.com/developers/applications")
        )
        id_row.addWidget(get_id_btn)
        c_layout.addLayout(id_row)

        self.broadcast_combo = QComboBox()
        self.broadcast_combo.addItems(["Discord pipe", "Vencord bridge", "Both"])
        self.broadcast_combo.setToolTip(
            "Where to announce your activity. 'Discord pipe' uses the local RPC "
            "connection (visible to you; others only if your app is verified). "
            "'Vencord bridge' posts to the bundled Vencord userplugin so friends "
            "see it without verification; 'Both' sends to both."
        )
        broadcast_row = QHBoxLayout()
        broadcast_row.setSpacing(8)
        broadcast_label = QLabel("Broadcast")
        broadcast_label.setObjectName("sectionLabel")
        broadcast_row.addWidget(broadcast_label)
        broadcast_row.addWidget(self.broadcast_combo, 1)
        c_layout.addLayout(broadcast_row)

        c_layout.addWidget(self._sep())

        grid = QHBoxLayout()
        grid.setSpacing(14)

        col1 = QVBoxLayout()
        col1.setSpacing(6)
        col1.addWidget(self._field_label("BROWSER"))
        self.browser_combo = QComboBox()
        self.browser_combo.addItems(["Chrome", "Brave", "Edge", "Opera"])
        col1.addWidget(self.browser_combo)

        col2 = QVBoxLayout()
        col2.setSpacing(6)
        col2.addWidget(self._field_label("POLL INTERVAL (S)"))
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(2, 60)
        self.interval_spin.setValue(int(self.cfg.data.poll_interval_seconds))
        col2.addWidget(self.interval_spin)
        col2.addWidget(self._field_label(""))
        col2.addStretch(1)

        grid.addLayout(col1, 1)
        grid.addLayout(col2, 1)
        c_layout.addLayout(grid)

        c_layout.addWidget(self._sep())

        c_layout.addWidget(self._field_label("OPEN A SITE AS PWA"))
        pwa_row = QHBoxLayout()
        pwa_row.setSpacing(8)
        self.pwa_url_edit = QLineEdit()
        self.pwa_url_edit.setPlaceholderText("https://miruro.tv")
        self.pwa_url_edit.returnPressed.connect(self._launch_pwa_url)
        pwa_row.addWidget(self.pwa_url_edit, 1)
        self.launch_pwa_btn = QPushButton("Launch PWA")
        self.launch_pwa_btn.setToolTip(
            "Open this URL in a standalone app-style window of your browser "
            "(works in Chrome and Brave)."
        )
        self.launch_pwa_btn.clicked.connect(self._launch_pwa_url)
        pwa_row.addWidget(self.launch_pwa_btn)
        c_layout.addLayout(pwa_row)

        c_layout.addWidget(self._sep())

        self.watch_any_btn = QPushButton("Watch anything…")
        self.watch_any_btn.setToolTip(
            "Force a presence for something AniPresence can't detect — an anime "
            "you're watching on a non-anime site, a movie, a game, anything."
        )
        c_layout.addWidget(self.watch_any_btn)
        manual_row = QHBoxLayout()
        manual_row.setSpacing(8)
        self.clear_manual_btn = QPushButton("Clear manual")
        self.clear_manual_btn.setObjectName("ghost")
        self.clear_manual_btn.setToolTip("Stop the manual presence and resume auto-detection.")
        self.stats_btn = QPushButton("Watch stats")
        self.stats_btn.setObjectName("ghost")
        self.stats_btn.setToolTip("Show how long you've watched each show (from your history).")
        manual_row.addWidget(self.clear_manual_btn)
        manual_row.addWidget(self.stats_btn)
        c_layout.addLayout(manual_row)

        c_layout.addWidget(self._sep())

        self.launch_chrome_btn = QPushButton("Launch Chrome")
        self.reconnect_btn = QPushButton("Reconnect Discord")
        self.clear_rpc_btn = QPushButton("Clear RPC")
        self.backup_btn = QPushButton("Backup config")
        self.backup_btn.setObjectName("ghost")
        self.backup_btn.setToolTip("Copy config.json to the Desktop")
        self.clear_rpc_btn.setObjectName("ghost")
        self.reconnect_btn.setObjectName("ghost")
        c_layout.addWidget(self.launch_chrome_btn)
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        action_row.addWidget(self.reconnect_btn)
        action_row.addWidget(self.clear_rpc_btn)
        c_layout.addLayout(action_row)
        c_layout.addWidget(self.backup_btn)

        c_layout.addStretch(1)
        left.addWidget(controls, 4)

    # --------------------------------------------------------------- stats
    def _build_stats_page(self, margin, compact):
        """Assemble the "Stats" tab: summary cards, a daily chart and per-title
        rows with a one-click Resume button that reopens the exact episode."""
        page = QScrollArea()
        page.setObjectName("scroller")
        page.setWidgetResizable(True)
        page.setFrameShape(QFrame.NoFrame)
        page.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        page.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(margin, (6 if not compact else 4), margin, margin)
        layout.setSpacing(self._gap)
        page.setWidget(host)
        self._stats_page = page

        # Summary cards (total time, streak, most-watched, titles watched).
        cards = QHBoxLayout()
        cards.setSpacing(self._gap)
        self._stat_cards = {}
        for key, caption in (
            ("total", "Total watch time"),
            ("streak", "Day streak"),
            ("most", "Most watched"),
            ("titles", "Shows watched"),
        ):
            card = QFrame()
            card.setObjectName("card")
            c = QVBoxLayout(card)
            c.setContentsMargins(self._pad, self._pad, self._pad, self._pad)
            c.setSpacing(4)
            value = QLabel("—")
            value.setObjectName("statValue")
            value.setWordWrap(True)
            cap = QLabel(caption)
            cap.setObjectName("sectionLabel")
            c.addWidget(value)
            c.addWidget(cap)
            cards.addWidget(card, 1)
            self._stat_cards[key] = value
        layout.addLayout(cards)

        # AniList backfill: seed the per-title totals from a public list.
        import_row = QHBoxLayout()
        import_row.setSpacing(8)
        import_row.addStretch(1)
        self.import_btn = QPushButton("Import from AniList")
        self.import_btn.setObjectName("ghost")
        self.import_btn.setToolTip(
            "Seed per-title watch time from your public AniList list "
            "(username in Controls). Does not affect the daily chart."
        )
        self.import_btn.clicked.connect(self._on_import_anilist)
        import_row.addWidget(self.import_btn)
        layout.addLayout(import_row)

        # Daily hours chart (QtCharts) — degrades to a hint label if missing.
        chart_card = QFrame()
        chart_card.setObjectName("card")
        cc = QVBoxLayout(chart_card)
        cc.setContentsMargins(self._pad, self._pad, self._pad, self._pad)
        cc.setSpacing(4)
        cc.addWidget(self._field_label("Last 14 days"))
        self._chart_view = None
        self._chart_hint = None
        if QTCHARTS_OK:
            view = QChartView()
            view.setRenderHint(QPainter.Antialiasing)
            # Compact chart so the per-title list below gets the room.
            view.setMinimumHeight(120)
            view.setMaximumHeight(160)
            self._chart_view = view
            cc.addWidget(view)
        else:
            hint = QLabel("Charts are unavailable (QtCharts missing).")
            hint.setObjectName("subLabel")
            self._chart_hint = hint
            cc.addWidget(hint)
        layout.addWidget(chart_card)

        # Sub-tabs under the chart: local watch time + AniList list sections.
        self._stats_tabs = QTabBar()
        self._stats_tabs.setDrawBase(False)
        for label in ("Watch time", "Watching", "Planning", "Completed"):
            self._stats_tabs.addTab(label)
        self._stats_tabs.setToolTip(
            "'Watch time' is your local totals; the AniList tabs show the lists "
            "imported for the active account."
        )
        self._stats_tabs.currentChanged.connect(self._on_stats_section_changed)
        layout.addWidget(self._stats_tabs)
        self._stats_stack = QStackedWidget()

        # "Watch time" page: per-title totals with a Resume button.
        watch_page = QWidget()
        watch_layout = QVBoxLayout(watch_page)
        watch_layout.setContentsMargins(0, 8, 0, 0)
        watch_layout.setSpacing(8)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_row.addWidget(self._field_label("Watch time by title"))
        title_row.addStretch(1)
        clear_btn = QPushButton("Clear stats")
        clear_btn.setObjectName("ghost")
        clear_btn.setToolTip("Reset all watch-time stats (this cannot be undone).")
        clear_btn.clicked.connect(self._on_clear_stats)
        title_row.addWidget(clear_btn)
        watch_layout.addLayout(title_row)
        self._stats_list = QListWidget()
        self._stats_list.setIconSize(QSize(38, 53))
        self._stats_list.itemDoubleClicked.connect(self._resume_stats_item)
        self._stats_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._stats_list.customContextMenuRequested.connect(self._stats_menu)
        watch_layout.addWidget(self._stats_list, 1)
        self._stats_stack.addWidget(watch_page)

        # One page per AniList section, filled from the imported lists.
        self._section_lists = {}
        self._section_icon_pending = {}
        for key in ("Watching", "Planning", "Completed"):
            page = QWidget()
            pl = QVBoxLayout(page)
            pl.setContentsMargins(0, 8, 0, 0)
            pl.setSpacing(8)
            pl.addWidget(
                self._field_label(
                    "From AniList · double-click to open the show's page"
                )
            )
            listw = QListWidget()
            listw.setIconSize(QSize(38, 53))
            listw.itemDoubleClicked.connect(self._open_anilist_entry)
            self._section_lists[key] = listw
            pl.addWidget(listw, 1)
            self._stats_stack.addWidget(page)

        layout.addWidget(self._stats_stack, 1)

    def _stats_menu(self, pos):
        item = self._stats_list.itemAt(pos)
        if item is None:
            return
        title = (item.data(Qt.UserRole + 1) or {}).get("title", "")
        if not title:
            return
        menu = QMenu(self)
        act_remove = menu.addAction("Remove from stats")
        choice = menu.exec(self._stats_list.viewport().mapToGlobal(pos))
        if choice is act_remove:
            self.controller.watchlog.remove_title(title)
            self._refresh_stats()
            self._log_line(f"Removed '{title}' from stats.")

    def _on_clear_stats(self):
        self.controller.watchlog.clear()
        self._refresh_stats()
        self._log_line("Watch-time stats cleared.")

    def _on_import_anilist(self):
        username = self.controller.active_anilist_account
        if not username:
            username, ok = QInputDialog.getText(
                self, "Import from AniList",
                "Your public AniList username (adds it as an account and imports "
                "its watch history into Stats):",
            )
            username = (username or "").strip()
            if not ok or not username:
                return
            if username not in self.controller.anilist_accounts:
                self.controller.add_anilist_account(username)
            else:
                self.controller.switch_anilist_account(username)
            self._populate_anilist_accounts()
            self._refresh_stats()
        self.import_btn.setEnabled(False)
        self.import_btn.setText("Importing…")
        self.controller.import_anilist_history(username)

    def _on_stats_imported(self, count, message):
        self.import_btn.setEnabled(True)
        self.import_btn.setText("Import from AniList")
        self._refresh_stats()
        if message:
            self._notify("AniPresence", message)

    def _resume_stats_item(self, item):
        url = item.data(Qt.UserRole) or ""
        if url:
            webbrowser.open(url)

    def _open_anilist_entry(self, item):
        url = item.data(Qt.UserRole) or ""
        if url:
            webbrowser.open(url)

    def _on_stats_section_changed(self, index):
        self._stats_stack.setCurrentIndex(index)

    def _refresh_sections(self):
        """Fill the Watching/Planning/Completed lists from the imported lists."""
        sections = self.controller.watchlog.anilist_sections()
        for key, listw in self._section_lists.items():
            listw.clear()
            entries = sections.get(key) or []
            if not entries:
                item = QListWidgetItem(
                    "No imported list for this section yet — use 'Import from AniList'."
                )
                item.setFlags(Qt.NoItemFlags)
                listw.addItem(item)
                continue
            for e in entries:
                title = e.get("title") or "?"
                progress = e.get("progress") or 0
                episodes = e.get("episodes")
                secs = float(e.get("seconds") or 0)
                if episodes:
                    prog = f"Ep {progress}/{episodes}"
                else:
                    prog = f"Ep {progress}" if progress else "—"
                label = f"{format_hours(secs):>8}  {title} · {prog}"
                item = QListWidgetItem(label)
                item.setData(Qt.UserRole, e.get("url") or "")
                if not self.cfg.data.privacy_mode and e.get("url"):
                    item.setToolTip(e["url"])
                item.setData(Qt.UserRole + 1, e)
                cover = e.get("cover") or ""
                if cover:
                    cached = self._cover_cache_path(cover)
                    if cached.exists():
                        item.setIcon(QIcon(str(cached)))
                    else:
                        self._section_icon_pending[cover] = item
                        if cover not in self._stats_icon_fetching:
                            self._stats_icon_fetching.add(cover)
                            self._nam.get(QNetworkRequest(QUrl(cover)))
                listw.addItem(item)

    def _refresh_stats(self):
        """Re-read the controller's WatchLog and repaint the Stats tab."""
        watchlog = self.controller.watchlog
        self._stat_cards["total"].setText(format_hours(watchlog.total_seconds()))
        self._stat_cards["streak"].setText(f"{watchlog.current_streak()}d")
        top = watchlog.top_titles(1)
        self._stat_cards["most"].setText(top[0]["title"] if top else "—")
        self._stat_cards["titles"].setText(str(len(watchlog.titles())))

        if self._chart_view is not None:
            self._update_stats_chart(watchlog.last_n_days(14))

        self._refresh_sections()

        self._stats_list.clear()
        self._stats_icon_pending.clear()
        titles = watchlog.top_titles(12)
        if not titles:
            item = QListWidgetItem("No watch time recorded yet — play an anime and it'll show up here.")
            item.setFlags(Qt.NoItemFlags)
            self._stats_list.addItem(item)
            return
        for title in titles:
            ep = title.get("episode")
            website = title.get("website", "")
            name = title["title"]
            label = f"{name} · Ep {ep} · {website}" if ep else f"{name} · {website}"
            item = QListWidgetItem(f"{format_hours(title.get('seconds', 0)):>8}  {label}")
            item.setData(Qt.UserRole, title.get("url") or "")
            if not self.cfg.data.privacy_mode and title.get("url"):
                item.setToolTip(title["url"])
            item.setData(Qt.UserRole + 1, title)
            cover = title.get("cover") or ""
            if cover:
                cached = self._cover_cache_path(cover)
                if cached.exists():
                    item.setIcon(QIcon(str(cached)))
                else:
                    self._stats_icon_pending[cover] = item
                    if cover not in self._stats_icon_fetching:
                        self._stats_icon_fetching.add(cover)
                        self._nam.get(QNetworkRequest(QUrl(cover)))
            self._stats_list.addItem(item)

    def _update_stats_chart(self, days):
        """Render *days* ([(date, seconds), ...]) as a per-day bar chart."""
        data = days or []
        series = QBarSeries()
        barset = QBarSet("Hours")
        accent = QColor(self.cfg.data.accent)
        if not accent.isValid():
            accent = QColor("#7c5cff")
        barset.setColor(accent)
        barset.setBorderColor(accent)
        for _, seconds in data:
            barset.append(round(seconds / 3600.0, 2))
        series.append(barset)

        chart = QChart()
        chart.addSeries(series)
        chart.legend().setVisible(False)
        chart.setBackgroundVisible(False)
        chart.setPlotAreaBackgroundVisible(False)

        axis_x = QBarCategoryAxis()
        axis_x.append([d[4:] if len(d) > 5 else d for d, _ in data])
        axis_x.setLabelsColor(QColor("#8a91a8"))
        axis_x.setGridLineVisible(False)
        chart.addAxis(axis_x, Qt.AlignBottom)
        series.attachAxis(axis_x)

        axis_y = QValueAxis()
        axis_y.setLabelFormat("%.0f h")
        axis_y.setLabelsColor(QColor("#8a91a8"))
        axis_y.setGridLineColor(QColor("#2a2d3a"))
        chart.addAxis(axis_y, Qt.AlignLeft)
        series.attachAxis(axis_y)

        chart.setAnimationOptions(QChart.NoAnimation)
        self._chart_view.setChart(chart)

    def _on_tab_changed(self, index):
        self.stack.setCurrentIndex(index)
        if index == self._stats_tab_index:
            self._refresh_stats()

    def _on_recent_also_stats(self, _entries):
        """Keep the Stats tab live while it's the active tab."""
        if self.tabbar.currentIndex() == self._stats_tab_index:
            self._refresh_stats()

    @staticmethod
    def _field_label(text):
        label = QLabel(text)
        label.setObjectName("sectionLabel")
        return label

    def _connect_signals(self):
        self.bridge.chrome_status.connect(self._on_chrome_status)
        self.bridge.discord_status.connect(self._on_discord_status)
        self.bridge.detection.connect(self._on_detection)
        self.bridge.recent.connect(self._on_recent)
        self.bridge.recent.connect(self._on_recent_also_stats)
        self.bridge.stats_imported.connect(self._on_stats_imported)
        self.bridge.account_avatar.connect(self._on_account_avatar)
        self.bridge.log.connect(self._on_log)

        self.rpc_check.toggled.connect(lambda on: self._on_rpc_toggled(on))
        self.detection_check.toggled.connect(lambda on: self.cfg.set(detection_enabled=on))
        self.auto_chrome_check.toggled.connect(lambda on: self.cfg.set(chrome_auto_launch=on))
        self.tray_check.toggled.connect(lambda on: self.cfg.set(minimize_to_tray=on))
        self.startup_check.toggled.connect(self._on_startup_toggled)
        self.privacy_check.toggled.connect(lambda on: self.cfg.set(privacy_mode=on))
        self.compact_check.toggled.connect(self._on_compact_toggled)
        self.idle_check.toggled.connect(lambda on: self.cfg.set(idle_presence=on))
        self.mica_check.toggled.connect(self._on_mica_toggled)
        self.hide_adult_check.toggled.connect(lambda on: self.cfg.set(hide_adult_content=on))
        self.anilist_combo.currentIndexChanged.connect(self._on_anilist_account_switch)
        self.interval_spin.valueChanged.connect(self._on_interval_changed)
        self.app_id_edit.editingFinished.connect(self._on_app_id_changed)
        self.broadcast_combo.currentIndexChanged.connect(self._on_broadcast_changed)
        self.browser_combo.currentIndexChanged.connect(self._on_browser_changed)
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        self.accent_btn.clicked.connect(self._on_pick_accent)
        self.backup_btn.clicked.connect(self._on_backup_config)
        self.win_close_btn.clicked.connect(self.close)
        self.win_min_btn.clicked.connect(self._animated_minimize)
        self.win_max_btn.clicked.connect(self._toggle_max)

        self.launch_chrome_btn.clicked.connect(self.controller.launch_chrome)
        self.reconnect_btn.clicked.connect(self.controller.reconnect_discord)
        self.clear_rpc_btn.clicked.connect(self.controller.clear_rpc)
        self.open_site_btn.clicked.connect(self._open_site)
        self.open_pwa_btn.clicked.connect(self._open_pwa)
        self.open_anilist_btn.clicked.connect(self._open_anilist)
        self.fix_match_btn.clicked.connect(self._on_wrong_match)
        self.next_ep_btn.clicked.connect(self._next_episode)
        self.copy_url_btn.clicked.connect(self._copy_url)
        self.watch_any_btn.clicked.connect(self._on_watch_any)
        self.clear_manual_btn.clicked.connect(self._on_clear_manual)
        self.stats_btn.clicked.connect(self._on_stats)
        self.recent_list.itemDoubleClicked.connect(self._open_recent_item)
        self.recent_list.customContextMenuRequested.connect(self._recent_menu)

    def _load_toggles_from_config(self):
        def load(widget, value):
            with QSignalBlocker(widget):
                widget.setChecked(value)
        load(self.rpc_check, self.cfg.data.rpc_enabled)
        load(self.detection_check, self.cfg.data.detection_enabled)
        load(self.auto_chrome_check, self.cfg.data.chrome_auto_launch)
        load(self.tray_check, self.cfg.data.minimize_to_tray)
        load(self.privacy_check, self.cfg.data.privacy_mode)
        load(self.compact_check, self.cfg.data.compact)
        load(self.idle_check, self.cfg.data.idle_presence)
        load(self.mica_check, self.cfg.data.mica_backdrop)
        load(self.hide_adult_check, self.cfg.data.hide_adult_content)
        from ..startup import is_enabled
        load(self.startup_check, self.cfg.data.start_windows and is_enabled())
        with QSignalBlocker(self.browser_combo):
            self.browser_combo.setCurrentIndex(BROWSER_INDEX[self.cfg.data.browser])
        with QSignalBlocker(self.theme_combo):
            self.theme_combo.setCurrentIndex(THEME_INDEX.get(self.cfg.data.theme, 0))
        with QSignalBlocker(self.broadcast_combo):
            self.broadcast_combo.setCurrentIndex(BROADCAST_INDEX.get(self.cfg.data.rpc_broadcast, 0))

    def _after_onboarding(self):
        """Refresh widgets after the first-run wizard changes the config."""
        self.app_id_edit.setText(self.cfg.data.discord_application_id)
        self._load_toggles_from_config()
        self._populate_anilist_accounts()
        self._refresh_stats()
        self.controller.reconnect_discord()

    # ------------------------------------------------------------ handlers
    def _apply_stylesheet(self):
        self._applied_theme = resolve_theme(self.cfg.data.theme)
        QApplication.instance().setStyleSheet(
            build_theme_qss(
                self._applied_theme,
                self.cfg.data.accent,
                self.cfg.data.compact,
                self.cfg.data.mica_backdrop,
            )
        )
        if self._cover_spinner is not None:
            self._cover_spinner.set_color(self.cfg.data.accent)
        self._apply_backdrop()

    def _maybe_resync_system_theme(self):
        """Live-follow Windows light/dark mode when the 'System' theme is active."""
        if self.cfg.data.theme != "system":
            return
        resolved = resolve_theme("system")
        if resolved != self._applied_theme:
            self._apply_stylesheet()

    def _on_mica_toggled(self, on):
        self.cfg.set(mica_backdrop=on)
        self._apply_stylesheet()

    def _on_theme_changed(self, index):
        theme = THEME_VALUE.get(index, "dark")
        self.cfg.set(theme=theme)
        self._apply_stylesheet()

    def _on_compact_toggled(self, on):
        self.cfg.set(compact=on)
        self._apply_stylesheet()
        self._rebuild_ui()

    def _on_pick_accent(self):
        current = QColor(self.cfg.data.accent)
        if not current.isValid():
            current = QColor("#7c5cff")
        color = QColorDialog.getColor(current, self, "Choose accent color")
        if color.isValid():
            self.cfg.set(accent=color.name())
            self.setWindowIcon(app_icon(64, color.name()))
            if self._tray:
                self._tray.setIcon(app_icon(64, color.name()))
            self._apply_stylesheet()

    def _on_startup_toggled(self, on):
        from ..startup import disable_startup, enable_startup
        ok = enable_startup() if on else disable_startup()
        self.cfg.set(start_windows=on and ok)
        self.startup_check.setChecked(on and ok)
        if not ok:
            self._notify("AniPresence", "Could not update Windows startup entry (only works in the packaged app).")

    def _on_backup_config(self):
        import shutil
        try:
            dest = Path.home() / "Desktop" / "anipresence_config_backup.json"
            dest.parent.mkdir(parents=True, exist_ok=True)
            self.cfg.save()
            shutil.copy(self.cfg.path, dest)
            self._notify("AniPresence", f"Backed up config to {dest}")
            self._log_line(f"Backed up config to {dest}")
        except OSError as exc:
            self._notify("AniPresence", f"Backup failed: {exc}")

    def _rebuild_ui(self):
        if getattr(self, "_rebuilding", False):
            return
        self._rebuilding = True
        try:
            self._do_rebuild()
        finally:
            self._rebuilding = False

    def _do_rebuild(self):
        for sig in (self.bridge.chrome_status, self.bridge.discord_status,
                    self.bridge.detection, self.bridge.recent, self.bridge.log,
                    self.bridge.stats_imported, self.bridge.account_avatar):
            try:
                sig.disconnect()
            except (TypeError, RuntimeError):
                pass
        self._build_ui()
        self._connect_signals()
        self._load_toggles_from_config()
        if getattr(self, "_last_recent", None) is not None:
            self._on_recent(self._last_recent)
        if self._current_payload:
            self._on_detection(self._current_payload)
        else:
            self.show_placeholder()
        self._update_cover_size()
        if self.tabbar.currentIndex() == self._stats_tab_index:
            self._refresh_stats()

    def _on_rpc_toggled(self, on):
        self.cfg.set(rpc_enabled=on)
        if not on:
            self.controller.clear_rpc()

    def _on_interval_changed(self, value):
        self.cfg.set(poll_interval_seconds=float(value))

    def _on_app_id_changed(self):
        app_id = self.app_id_edit.text().strip()
        self.cfg.set(discord_application_id=app_id)
        if app_id:
            # Saved -> make the worker try to (re)connect right away.
            self.controller.reconnect_discord()

    def _on_broadcast_changed(self, index):
        broadcast = BROADCAST_VALUE.get(index, "discord")
        self.cfg.set(rpc_broadcast=broadcast)
        self._log_line(f"Rich Presence broadcast set to '{broadcast}'.")
        if broadcast != "vencord":
            self.controller.reconnect_discord()

    def _populate_anilist_accounts(self):
        """Fill the account dropdown; index 0 is the shared Default profile."""
        accounts = self.controller.anilist_accounts
        active = self.controller.active_anilist_account
        with QSignalBlocker(self.anilist_combo):
            self.anilist_combo.clear()
            self.anilist_combo.addItem("Default", "")
            for name in accounts:
                self.anilist_combo.addItem(name, name)
            idx = self.anilist_combo.findData(active)
            self.anilist_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._load_account_avatar()

    def _load_account_avatar(self):
        """Request the avatar for the currently active account (monogram while
        it loads, cleared on the shared Default profile)."""
        name = self.controller.active_anilist_account or ""
        if not name:
            self.account_avatar.clear()
            self.account_avatar.setText("")
            self._account_avatar_url = ""
            return
        self.account_avatar.setText(name[:1].upper())
        self.controller.fetch_account_avatar(name)

    def _on_account_avatar(self, username, url):
        if username != self.controller.active_anilist_account:
            return  # switched away while the lookup was in flight
        self._account_avatar_url = url or ""
        if not url:
            self.account_avatar.setText(username[:1].upper())
            return
        cached = self._cover_cache_path(url)
        if cached.exists():
            self._set_account_avatar_pixmap(cached)
            return
        self._avatar_pending_reply[url] = self.account_avatar
        if url not in self._avatar_fetching:
            self._avatar_fetching.add(url)
            self._nam.get(QNetworkRequest(QUrl(url)))

    def _set_account_avatar_pixmap(self, path):
        """Show a loaded avatar as a 34px round image on the indicator."""
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            return
        size = self.account_avatar.width() or 34
        scaled = pixmap.scaled(
            size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
        )
        round_pm = QPixmap(size, size)
        round_pm.fill(Qt.transparent)
        painter = QPainter(round_pm)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(Qt.white)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(0, 0, size, size)
        painter.setClipRect(round_pm.rect())
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.drawPixmap((size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled)
        painter.end()
        self.account_avatar.setPixmap(round_pm)

    def _on_anilist_account_switch(self, index):
        name = self.anilist_combo.itemData(index) or ""
        if name != self.controller.active_anilist_account:
            self.controller.switch_anilist_account(name)
            self._refresh_stats()
        self._load_account_avatar()

    def _on_add_anilist_account(self):
        username, ok = QInputDialog.getText(
            self, "Add AniList account",
            "Your public AniList username (each account gets its own watch-history save file):",
        )
        username = (username or "").strip()
        if not ok or not username:
            return
        if username in self.controller.anilist_accounts:
            self._notify("AniPresence", "That account is already added.")
            return
        self.controller.add_anilist_account(username)
        self._populate_anilist_accounts()
        self._refresh_stats()
        self._log_line(f"Added AniList account {username}.")

    def _on_remove_anilist_account(self):
        index = self.anilist_combo.currentIndex()
        name = self.anilist_combo.itemData(index) or ""
        if not name:
            self._notify("AniPresence", "Select an account to remove.")
            return
        answer = QMessageBox.question(
            self, "Remove account",
            f"Remove AniList account '{name}'? Its save file is kept, so you can "
            "re-add it later.",
        )
        if answer != QMessageBox.Yes:
            return
        self.controller.remove_anilist_account(name)
        self._populate_anilist_accounts()
        self._refresh_stats()
        self._log_line(f"Removed AniList account {name}.")

    def _on_browser_changed(self, index):
        browser = BROWSER_VALUE.get(index, "chrome")
        self.cfg.set(browser=browser)
        self.controller.chrome.browser = browser
        label = self._browser_label(browser)
        self.launch_chrome_btn.setText(label)
        self.launch_chrome_btn.setToolTip(f"Launch {BROWSER_NAMES.get(browser, 'Chrome')}")
        self._render_browser_chip()

    @staticmethod
    def _browser_label(browser):
        labels = {"chrome": "Launch Chrome", "brave": "Launch Brave", "edge": "Launch Edge", "opera": "Launch Opera"}
        return labels.get(browser, "Launch Chrome")

    def _on_chrome_status(self, text, connected):
        self._last_chrome_status = text
        prev = self._prev_chrome_ok
        self._prev_chrome_ok = connected
        self._render_browser_chip()
        if connected and prev is False:
            self._notify("AniPresence", "Connected to the browser.")
        elif not connected and prev is True:
            self._notify("AniPresence", "Lost connection to the browser.")

    def _render_browser_chip(self):
        """Paint the connection chip with the currently selected browser's name."""
        browser = BROWSER_NAMES.get(self.cfg.data.browser, "Chrome")
        connected = self._prev_chrome_ok if self._prev_chrome_ok is not None else False
        text = self._last_chrome_status or "…"
        self.chrome_chip.setText(f"{browser} · {text}")
        self.chrome_chip.setObjectName("chipOk" if connected else "chipBad")
        self.chrome_chip.style().unpolish(self.chrome_chip)
        self.chrome_chip.style().polish(self.chrome_chip)

    def _on_discord_status(self, text, connected):
        self.discord_chip.setText(f"Discord · {text}")
        self.discord_chip.setObjectName("chipOk" if connected else "chipBad")
        self.discord_chip.style().unpolish(self.discord_chip)
        self.discord_chip.style().polish(self.discord_chip)
        if connected and self._prev_discord_ok is False:
            self._notify("AniPresence", "Connected to Discord Rich Presence.")
        elif not connected and self._prev_discord_ok is True:
            self._notify("AniPresence", "Lost Discord Rich Presence.")
        self._prev_discord_ok = connected

    def _on_detection(self, payload):
        if payload.get("clear"):
            self.show_placeholder()
            return
        self._current_payload = payload
        title = payload.get("title") or "Unknown title"
        self.anime_title.setText(title)

        parts = []
        if payload.get("episode"):
            parts.append(f"Episode {payload['episode']}")
        if payload.get("website"):
            parts.append(f"on {payload['website']}")
        self.episode_label.setText(" · ".join(parts))

        if payload.get("anilist_url"):
            conf = payload.get("match_confidence", 0)
            self.match_label.setText(
                f"AniList match ({conf:.0f}% confidence)"
            )
        else:
            self.match_label.setText("No AniList match — showing raw detection")

        duration = float(payload.get("duration") or 0)
        current = float(payload.get("current_time") or 0)
        if duration > 0:
            self.progress.setValue(int(min(1000, 1000 * current / duration)))
        else:
            self.progress.setValue(0)

        from ..discord.rpc import format_seconds
        self.time_cur.setText(format_seconds(current))
        self.time_total.setText(format_seconds(duration))

        self.open_site_btn.setEnabled(bool(payload.get("url")))
        self.open_pwa_btn.setEnabled(bool(payload.get("url")))
        self.open_anilist_btn.setEnabled(bool(payload.get("anilist_url")))
        self.fix_match_btn.setEnabled(bool(payload.get("url")))
        self.next_ep_btn.setEnabled(bool(increment_episode_url(payload.get("url"))))
        self.copy_url_btn.setEnabled(bool(payload.get("url")))
        if self._tray:
            self._tray_act_site.setEnabled(bool(payload.get("url")))
            self._tray_act_pwa.setEnabled(bool(payload.get("url")))
            self._tray.setToolTip(f"AniPresence · {title}")

        cover = payload.get("cover_url") or ""
        if cover and cover != self._cover_url:
            self._cover_url = cover
            self._request_cover(cover)
        elif self._cover_pixmap and cover:
            self.cover_label.setPixmap(
                self._cover_pixmap.scaled(
                    self.cover_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
            )

    def _on_recent(self, entries):
        self._last_recent = entries
        self._recent_icon_pending.clear()
        self.recent_list.clear()
        if not entries:
            item = QListWidgetItem("Nothing watched yet — play an anime in your browser.")
            item.setFlags(Qt.NoItemFlags)
            self.recent_list.addItem(item)
            return
        for entry in entries:
            title = entry.get("title", "?")
            ep = entry.get("episode")
            site = entry.get("website", "")
            label = f"{title} · Ep {ep} · {site}" if ep else f"{title} · {site}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, entry)
            if not self.cfg.data.privacy_mode:
                item.setToolTip(entry.get("url") or "")
            cover = entry.get("cover") or ""
            if cover:
                cached = self._cover_cache_path(cover)
                if cached.exists():
                    item.setIcon(QIcon(str(cached)))
                else:
                    self._recent_icon_pending[cover] = item
                    if cover not in self._recent_icon_fetching:
                        self._recent_icon_fetching.add(cover)
                        self._nam.get(QNetworkRequest(QUrl(cover)))
            self.recent_list.addItem(item)

    def _open_recent_item(self, item):
        entry = item.data(Qt.UserRole) or {}
        url = entry.get("url")
        if url:
            webbrowser.open(url)

    def _recent_menu(self, pos):
        item = self.recent_list.itemAt(pos)
        if item is None:
            return
        entry = item.data(Qt.UserRole) or {}
        url = entry.get("url")
        if not url:
            return
        menu = QMenu(self)
        act_open = menu.addAction("Open in browser")
        act_pwa = menu.addAction("Open as PWA")
        menu.addSeparator()
        act_remove = menu.addAction("Remove from history")
        choice = menu.exec(self.recent_list.viewport().mapToGlobal(pos))
        if choice is act_open:
            webbrowser.open(url)
        elif choice is act_pwa:
            self.controller.launch_pwa(url)
        elif choice is act_remove:
            self.controller.remove_recent_entry(entry)

    def _on_clear_history(self):
        self.controller.clear_recent()
        self._log_line("Watch history cleared.")

    def _on_clear_log(self):
        self.log_view.clear()

    # ----------------------------------------------------------------- about
    def _on_about(self):
        from .. import __version__ as ver
        from ..config import APP_DIR

        dialog = QDialog(self)
        dialog.setWindowTitle("About AniPresence")
        layout = QVBoxLayout(dialog)
        layout.setSpacing(10)

        icon = app_icon(72, self.cfg.data.accent)
        icon_label = QLabel()
        icon_label.setPixmap(icon.pixmap(72, 72))
        icon_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(icon_label)

        title = QLabel("AniPresence")
        title.setObjectName("appTitle")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        version = QLabel(f"Version {ver}")
        version.setObjectName("versionLabel")
        version.setAlignment(Qt.AlignCenter)
        layout.addWidget(version)

        desc = QLabel(
            "Detects the anime playing in your browser and mirrors it to "
            "Discord Rich Presence via AniList."
        )
        desc.setObjectName("subLabel")
        desc.setWordWrap(True)
        desc.setAlignment(Qt.AlignCenter)
        layout.addWidget(desc)

        if not self.cfg.data.privacy_mode:
            data = QLabel(f"Data folder: {APP_DIR}")
            data.setObjectName("subLabel")
            data.setWordWrap(True)
            data.setAlignment(Qt.AlignCenter)
            layout.addWidget(data)

        update_btn = QPushButton("Check for updates")
        update_btn.setObjectName("ghost")
        update_btn.clicked.connect(lambda: self._check_for_updates(dialog))
        layout.addWidget(update_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn)

        dialog.resize(360, 380)
        dialog.exec()

    def _check_for_updates(self, parent=None):
        """Compare the local version against an update feed (if configured)."""
        feed = (self.cfg.data.update_feed_url or "").strip()
        if not feed:
            self._log_line("No update feed configured (set update_feed_url in config.json).")
            self._notify("AniPresence", "No update feed configured.")
            return

        def done(ok, latest):
            if not ok:
                self._notify("AniPresence", "Update check failed (network error).")
                return
            current_parts = _version_tuple(__version__)
            latest_parts = _version_tuple(latest)
            if not current_parts or not latest_parts:
                self._notify("AniPresence", f"Could not parse versions ({latest}).")
                return
            if latest_parts > current_parts:
                self._notify(
                    "AniPresence",
                    f"Update available: {latest} (you have {__version__}).",
                )
            else:
                self._notify(
                    "AniPresence",
                    f"You're up to date ({__version__}).",
                )

        self._log_line(f"Checking for updates against {feed} ...")
        # Dedicated manager: the shared _nam routes replies through _on_cover_reply,
        # which would misread a JSON feed as an image.
        nam = QNetworkAccessManager(self)
        req = QNetworkRequest(QUrl(feed))
        reply = nam.get(req)

        def on_reply():
            err = reply.error()
            latest = ""
            if err == QNetworkReply.NoError:
                import json as _json
                try:
                    latest = _json.loads(bytes(reply.readAll()).decode("utf-8", "ignore")).get("version", "")
                except (ValueError, AttributeError):
                    latest = ""
            reply.deleteLater()
            done(err == QNetworkReply.NoError, latest)

        reply.finished.connect(on_reply)

    def _on_log(self, line):
        self.log_view.appendPlainText(
            redact_urls(line) if self.cfg.data.privacy_mode else line
        )

    def _log_line(self, message):
        self._on_log(message)

    # ------------------------------------------------------------ cover art
    def _cover_cache_path(self, url):
        ext = Path(urlsplit(url).path).suffix.lower() or ".jpg"
        name = hashlib.sha1(url.encode("utf-8", "ignore")).hexdigest()[:20] + ext
        return self._cover_cache_dir / name

    def _request_cover(self, url):
        cached = self._cover_cache_path(url)
        if cached.exists():
            pixmap = QPixmap(str(cached))
            if not pixmap.isNull():
                self._cover_pixmap = pixmap
                self.cover_label.setPixmap(
                    pixmap.scaled(self.cover_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
                self._cover_spinner.hide()
                return
        self._cover_requested = True
        self._cover_spinner.show()
        request = QNetworkRequest(QUrl(url))
        self._nam.get(request)

    def _on_cover_reply(self, reply):
        orig = reply.request().url().toString()
        pending = None
        if orig in self._avatar_pending_reply:
            # Account-avatar image for the account indicator.
            self._avatar_fetching.discard(orig)
            label = self._avatar_pending_reply.pop(orig)
            if reply.error() == QNetworkReply.NoError:
                data = bytes(reply.readAll())
                cached = self._cover_cache_path(orig)
                try:
                    if not self._cover_cache_dir.exists():
                        self._cover_cache_dir.mkdir(parents=True, exist_ok=True)
                    if not cached.exists():
                        cached.write_bytes(data)
                except OSError:
                    pass
                self._set_account_avatar_pixmap(cached)
            reply.deleteLater()
            return
        if orig in self._recent_icon_pending:
            # History thumbnail: cache it and drop it onto its list item.
            self._recent_icon_fetching.discard(orig)
            item = self._recent_icon_pending.get(orig)
            pending = self._recent_icon_pending
            pending.pop(orig, None)
        elif orig in self._stats_icon_pending:
            # Stats-list cover: same cache-and-drop flow.
            self._stats_icon_fetching.discard(orig)
            item = self._stats_icon_pending.get(orig)
            pending = self._stats_icon_pending
            pending.pop(orig, None)
        elif orig in self._section_icon_pending:
            # AniList-section cover (Watching/Planning/Completed tabs).
            self._stats_icon_fetching.discard(orig)
            item = self._section_icon_pending.get(orig)
            pending = self._section_icon_pending
            pending.pop(orig, None)
        else:
            item = None
        if item is not None:
            if reply.error() == QNetworkReply.NoError:
                data = bytes(reply.readAll())
                pixmap = QPixmap()
                if pixmap.loadFromData(data):
                    cached = self._cover_cache_path(orig)
                    try:
                        if not self._cover_cache_dir.exists():
                            self._cover_cache_dir.mkdir(parents=True, exist_ok=True)
                        if not cached.exists():
                            cached.write_bytes(data)
                    except OSError:
                        pass
                    if item.listWidget() is not None:
                        item.setIcon(QIcon(str(cached)))
            reply.deleteLater()
            return

        self._cover_requested = False
        self._cover_spinner.hide()
        if reply.error() != QNetworkReply.NoError:
            reply.deleteLater()
            return
        data = bytes(reply.readAll())
        url = reply.url().toString() or self._cover_url
        reply.deleteLater()
        if not data:
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            return
        self._cover_pixmap = pixmap
        size = self.cover_label.size()
        self.cover_label.setPixmap(
            pixmap.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )
        try:
            if not self._cover_cache_dir.exists():
                self._cover_cache_dir.mkdir(parents=True, exist_ok=True)
            cached = self._cover_cache_path(url)
            if not cached.exists():
                cached.write_bytes(data)
        except OSError:
            pass

    def _place_cover_spinner(self):
        """Center the loading spinner over whichever cover size is active."""
        sp = self._cover_spinner
        if sp is None:
            return
        size = sp.sizeHint()
        sp.resize(size)
        w = self.cover_label.width()
        h = self.cover_label.height()
        if w > 0 and h > 0:
            sp.move((w - size.width()) // 2, (h - size.height()) // 2)

    def _update_cover_size(self):
        """Shrink the cover art so the text column stays readable on narrow panes."""
        pane = self._left_pane
        if pane is None:
            return
        avail = pane.width()
        if avail <= 0:
            return
        base = 185 if self.cfg.data.compact else 230
        reserve = 300 if self.cfg.data.compact else 300
        width = min(base, max(70, avail - reserve))
        height = round(width * 7 / 5)
        if self.cover_label.width() == width:
            return
        self.cover_label.setFixedSize(width, height)
        self._place_cover_spinner()
        if self._cover_pixmap:
            self.cover_label.setPixmap(
                self._cover_pixmap.scaled(
                    width, height, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
            )

    def resizeEvent(self, event):  # noqa: N802 (Qt naming)
        self._update_header_for_width()
        self._update_cover_size()
        super().resizeEvent(event)

    # --------------------------------------------------------------- actions
    @staticmethod
    def _normalize_url(text):
        text = text.strip()
        if not text:
            return ""
        if "://" not in text:
            text = "https://" + text
        return text

    def _open_site(self):
        if self._current_payload and self._current_payload.get("url"):
            webbrowser.open(self._current_payload["url"])

    def _open_pwa(self):
        url = self._current_payload.get("url") if self._current_payload else None
        if url:
            self.controller.launch_pwa(url)

    def _launch_pwa_url(self):
        url = self._normalize_url(self.pwa_url_edit.text())
        if not url:
            return
        self.pwa_url_edit.setText(url)
        self.controller.launch_pwa(url)

    def _open_anilist(self):
        if self._current_payload and self._current_payload.get("anilist_url"):
            webbrowser.open(self._current_payload["anilist_url"])

    def _on_wrong_match(self):
        url = self._current_payload.get("url") if self._current_payload else None
        if not url:
            return
        current = self._current_payload
        title, ok = QInputDialog.getText(
            self, "Wrong match?",
            "Correct title:",
            text=current.get("detected_title") or current.get("title") or "",
        )
        if not ok or not title.strip():
            return
        ep_str, ok2 = QInputDialog.getText(
            self, "Wrong match?",
            "Correct episode (leave empty to keep the detected one):",
            text=str(current.get("episode") or ""),
        )
        episode = None
        if ok2 and ep_str.strip():
            try:
                episode = int(float(ep_str.strip()))
            except ValueError:
                episode = ep_str.strip()
        self.controller.set_manual_override(url, title.strip(), episode)
        self._log_line(f"Manual override saved for {url}")
        self._notify("AniPresence", "Override saved. It applies on the next detection tick.")

    def _next_episode(self):
        url = self._current_payload.get("url") if self._current_payload else None
        nxt = increment_episode_url(url) if url else None
        if nxt:
            webbrowser.open(nxt)
            self._log_line(f"Opening next episode: {nxt}")

    def _copy_url(self):
        url = self._current_payload.get("url") if self._current_payload else None
        if url:
            QApplication.clipboard().setText(url)
            self._notify("AniPresence", "Page URL copied to clipboard.")

    def _on_watch_any(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Watch anything")
        form = QFormLayout(dialog)
        form.setLabelAlignment(Qt.AlignLeft)
        title_edit = QLineEdit()
        title_edit.setPlaceholderText("e.g. Vinland Saga")
        form.addRow("Title:", title_edit)
        ep_edit = QLineEdit()
        ep_edit.setPlaceholderText("Optional episode number")
        form.addRow("Episode:", ep_edit)
        site_edit = QLineEdit()
        site_edit.setPlaceholderText("e.g. Crunchyroll")
        form.addRow("Website:", site_edit)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Show presence")
        buttons.button(QDialogButtonBox.Cancel).setText("Cancel")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        title_edit.setFocus()
        if dialog.exec() != QDialog.Accepted:
            return
        title = title_edit.text().strip()
        if not title:
            return
        episode = ep_edit.text().strip()
        website = site_edit.text().strip() or "Watching"
        self.controller.set_manual_presence(title, episode, website)
        self.show_manual_card(title, episode, website)
        self._log_line(f"Manual presence: {title}" + (f" · Episode {episode}" if episode else "") + f" · {website}")
        self._notify("AniPresence", "Manual presence is now shown.")

    def show_manual_card(self, title, episode="", website=""):
        """Fill the card with a manual-presence payload (auto-detection is paused)."""
        self._current_payload = {"title": title, "episode": episode, "website": website, "url": ""}
        self.anime_title.setText(title)
        parts = []
        if episode:
            parts.append(f"Episode {episode}")
        if website:
            parts.append(f"on {website}")
        self.episode_label.setText(" · ".join(parts) or "MANUAL PRESENCE")
        self.match_label.setText("Manual presence — auto-detection paused")
        self.progress.setValue(0)
        self.time_cur.setText("00:00")
        self.time_total.setText("00:00")
        self.cover_label.clear()
        self.cover_label.setText("Manual")
        self.open_site_btn.setEnabled(False)
        self.open_pwa_btn.setEnabled(False)
        self.open_anilist_btn.setEnabled(False)
        self.fix_match_btn.setEnabled(False)
        self.next_ep_btn.setEnabled(False)
        self.copy_url_btn.setEnabled(False)
        if self._tray:
            self._tray_act_site.setEnabled(False)
            self._tray_act_pwa.setEnabled(False)
            self._tray.setToolTip(f"AniPresence · {title}")

    def _on_clear_manual(self):
        self.controller.clear_manual_presence()
        self.show_placeholder()
        self._log_line("Manual presence cleared; auto-detection resumed.")
        self._notify("AniPresence", "Manual presence cleared.")

    def _on_stats(self):
        self.tabbar.setCurrentIndex(self._stats_tab_index)

    # ------------------------------------------------------------ backdrop
    def _apply_backdrop(self):
        """Modern window chrome: Mica/acrylic backdrop + themed title bar.

        Best effort on Windows only. When Mica is on, the app's panels are
        translucent so the OS backdrop shows through as frosted glass.
        """
        if not sys.platform.startswith("win"):
            return
        try:
            import ctypes
            from ctypes import wintypes
        except ImportError:
            return
        try:
            handle = int(self.winId())
            dark = 1 if self.cfg.data.theme != "light" else 0
            attr = ctypes.windll.dwmapi.DwmSetWindowAttribute
            attr.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
            dark_var = ctypes.c_int(dark)
            attr(handle, 20, ctypes.byref(dark_var), ctypes.sizeof(dark_var))  # immersive dark mode
            corner = ctypes.c_int(2)  # DWMWA_WINDOW_CORNER_PREFERENCE = 33, value 2 = ROUND
            attr(handle, 33, ctypes.byref(corner), ctypes.sizeof(corner))
            if self.cfg.data.mica_backdrop:
                mica_var = ctypes.c_int(2)  # DWMWA_SYSTEMBACKDROP_TYPE = 38, value 2 = MICA
                if attr(handle, 38, ctypes.byref(mica_var), ctypes.sizeof(mica_var)) == 0:
                    return
                # Windows 10 fallback: acrylic blur behind the client area.
                self._apply_acrylic(handle)
        except (OSError, AttributeError):
            pass

    def _apply_acrylic(self, handle):
        try:
            import ctypes
            from ctypes import wintypes

            ACCENT_ENABLE_ACRYLICBLURBEHIND = 4

            class AccentPolicy(ctypes.Structure):
                _fields_ = [
                    ("AccentState", ctypes.c_int),
                    ("AccentFlags", ctypes.c_int),
                    ("GradientColor", ctypes.c_uint),
                    ("AnimationId", ctypes.c_int),
                ]

            class WindowCompositionAttributeData(ctypes.Structure):
                _fields_ = [
                    ("Attribute", ctypes.c_int),
                    ("Data", ctypes.c_void_p),
                    ("SizeOfData", ctypes.c_size_t),
                ]

            # ABGR tint of the app background so text stays legible (alpha fades in).
            effective = self._applied_theme or resolve_theme(self.cfg.data.theme)
            if effective == "light":
                tint = 0xDDF4F5F7
            else:
                tint = 0xDD0E0F14
            accent = AccentPolicy(ACCENT_ENABLE_ACRYLICBLURBEHIND, 0, tint, 0)
            data = WindowCompositionAttributeData(
                19,  # WCA_ACCENT_POLICY
                ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p),
                ctypes.sizeof(accent),
            )
            ctypes.windll.user32.SetWindowCompositionAttribute(handle, ctypes.byref(data))

            class MARGINS(ctypes.Structure):
                _fields_ = [
                    ("cxLeftWidth", ctypes.c_int), ("cxRightWidth", ctypes.c_int),
                    ("cyTopHeight", ctypes.c_int), ("cyBottomHeight", ctypes.c_int),
                ]

            margins = MARGINS(-1, -1, -1, -1)
            ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(handle, ctypes.byref(margins))
        except (OSError, AttributeError):
            pass

    def showEvent(self, event):  # noqa: N802 (Qt naming)
        if self._start_maximized:
            self._start_maximized = False
            self.showMaximized()
        self.setWindowOpacity(1.0)
        self._apply_backdrop()
        super().showEvent(event)

    def show_placeholder(self):
        self._current_payload = None
        self.anime_title.setText("No anime detected")
        self.episode_label.setText("WATCHING A VIDEO IS ALL IT TAKES")
        self.match_label.setText("")
        self.progress.setValue(0)
        self.time_cur.setText("00:00")
        self.time_total.setText("00:00")
        self.cover_label.clear()
        self.cover_label.setText("Cover")
        self._cover_pixmap = None
        self._cover_spinner.hide()
        self.open_site_btn.setEnabled(False)
        self.open_pwa_btn.setEnabled(False)
        self.open_anilist_btn.setEnabled(False)
        self.fix_match_btn.setEnabled(False)
        self.next_ep_btn.setEnabled(False)
        self.copy_url_btn.setEnabled(False)
        if self._tray:
            self._tray_act_site.setEnabled(False)
            self._tray_act_pwa.setEnabled(False)
            self._tray.setToolTip("AniPresence")

    def _animated_hide_to_tray(self):
        """Fade the window out, then tuck it away in the system tray."""
        if self._fade_anim is not None:
            self._fade_anim.stop()
        self._fade_anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade_anim.setDuration(240)
        self._fade_anim.setStartValue(1.0)
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.setEasingCurve(QEasingCurve.InCubic)

        def _finish():
            self._fade_anim = None
            self.setWindowOpacity(1.0)
            self.hide()
            self._notify(
                "AniPresence",
                "Still running, minimized to the tray. Double-click to reopen.",
            )

        self._fade_anim.finished.connect(_finish)
        self._fade_anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    def closeEvent(self, event):  # noqa: N802 (Qt naming)
        if not self._quitting and self.cfg.data.minimize_to_tray and self._tray and self._tray.isVisible():
            event.ignore()
            self._animated_hide_to_tray()
            return
        self._save_window_state()
        self._unregister_hotkey()
        self.controller.request_stop()
        super().closeEvent(event)
