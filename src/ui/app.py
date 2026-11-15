"""PySide6 main window.

Displays live detection state (cover art, title, episode, progress), connection
status for Chrome and Discord, control toggles, recently-watched history, and a
scrollback log. The worker thread updates it exclusively through signals.
"""
import ctypes
import hashlib
import sys
import time
import webbrowser
from ctypes import wintypes
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import (
    QAbstractNativeEventFilter, QAbstractAnimation, QEasingCurve, QEvent, QPoint,
    QPropertyAnimation, QRect, QRectF, QMargins, QSignalBlocker, QSize, Qt, QTimer, QUrl,
)
from PySide6.QtGui import (
    QBrush, QColor, QFont, QIcon, QImage, QLinearGradient, QPainter, QPen, QPixmap,
    QRadialGradient,
)
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from .anime_list import AnimeList, DETAIL_ROLE
from .v3_widgets import IllustratedHeader, IllustratedSidebar, Toggle
from .v3_pages import HistoryPanel, ActivityPanel, AnalyticsPanel
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QFrame, QGraphicsOpacityEffect, QGridLayout, QHBoxLayout,
    QInputDialog, QLabel, QLayout, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QSpinBox, QStackedWidget, QSystemTrayIcon, QTabBar, QToolButton,
    QVBoxLayout, QWidget,
)

# ---------------------------------------------------------------------------
# Native Windows 11 window animations for the frameless window.
#
# A Qt FramelessWindowHint window is a WS_POPUP, which the DWM will NOT play
# the normal minimize/restore/maximize animations for. The fix (used by
# PyQt-Frameless-Window, Chromium and Discord) is to keep the window frameless
# but re-add the caption frame bits after creation: DWM then treats us like a
# regular window for animation purposes. WM_NCCALCSIZE is handled to (a) hide
# the OS caption (client = full window) and (b) keep the maximized client
# inside the monitor work area so the taskbar stays visible.
# ---------------------------------------------------------------------------
WM_NCCALCSIZE = 0x0083
GWL_STYLE = -16
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_SYSMENU = 0x00080000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
SWP_FRAMECHANGED = 0x0020
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SW_MAXIMIZE = 3
SW_RESTORE = 9
WVR_REDRAW = 0x0301
_NATIVE_STYLES = WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", wintypes.DWORD),
    ]


class _NCCALCSIZE_PARAMS(ctypes.Structure):
    _fields_ = [
        ("rgrc0", _RECT),
        ("rgrc1", _RECT),
        ("rgrc2", _RECT),
        ("lppos", ctypes.c_void_p),
    ]


def _declare_user32():
    """Set exact 64-bit signatures so ctypes never truncates HWNDs/long ptrs."""
    user32 = ctypes.windll.user32
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]
    user32.IsZoomed.restype = wintypes.BOOL
    user32.IsZoomed.argtypes = [wintypes.HWND]
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    user32.GetMonitorInfoW.argtypes = [wintypes.HWND, ctypes.POINTER(_MONITORINFO)]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    return user32
from PySide6.QtWidgets import QSizePolicy

try:  # QtCharts is optional; the stats chart degrades to a plain label if absent.
    from PySide6.QtCharts import (
        QBarCategoryAxis, QBarSeries, QBarSet, QChart, QChartView, QValueAxis,
    )
    QTCHARTS_OK = True
except Exception:  # noqa: BLE001
    QTCHARTS_OK = False

from .. import __version__
from ..anime.titles import format_episode_progress, format_season, next_airing_label
from ..config import APP_DIR, ConfigManager, redact_urls
from ..core.controller import Controller
from ..core.watchlog import format_hours
from ..discord.rpc import format_seconds
from ..theme import resolve_theme
from ..ui.bridge import UiBridge
from ..urlnav import increment_episode_url

BROWSER_INDEX = {"chrome": 0, "brave": 1, "edge": 2, "opera": 3}
BROWSER_VALUE = {v: k for k, v in BROWSER_INDEX.items()}
BROWSER_NAMES = {"chrome": "Chrome", "brave": "Brave", "edge": "Edge", "opera": "Opera"}

THEME_INDEX = {"dark": 0, "light": 1, "system": 2, "oled": 3}
THEME_VALUE = {v: k for k, v in THEME_INDEX.items()}

BROADCAST_INDEX = {"discord": 0, "vencord": 1, "both": 2}
BROADCAST_VALUE = {v: k for k, v in BROADCAST_INDEX.items()}

# Curated accent palettes (name, hex) — one per season plus a few extras.
ACCENT_PRESETS = [
    ("Spring Bloom", "#34d399"),
    ("Summer Sunset", "#fb923c"),
    ("Autumn Ember", "#f87171"),
    ("Winter Frost", "#38bdf8"),
    ("Cherry Blossom", "#f472b6"),
    ("Midnight Violet", "#7c5cff"),
    ("Ocean Depths", "#22d3ee"),
    ("Forest Dew", "#a3e635"),
]
CUSTOM_ACCENT = "__custom__"

DARK_QSS = """
QWidget { background-color: #0e0f14; color: #e7e9ee; font-family: "Figtree", "Segoe UI Variable", "Segoe UI", sans-serif; font-size: 13px; }
QMainWindow { background-color: #0e0f14; }
QLabel#appTitle { font-size: 18px; font-weight: 750; color: #ffffff; letter-spacing: 0.2px; }
QLabel#titleLabel { font-size: 26px; font-weight: 700; color: #ffffff; letter-spacing: -0.2px; }
QLabel#subLabel { color: #8a91a8; font-size: 12px; }
QLabel#versionLabel { color: #5a6178; font-size: 10px; letter-spacing: 0.5px; }
QLabel#captionLabel { color: #8b6cff; font-size: 13px; font-weight: 700; letter-spacing: 0.3px; }
QLabel#sectionLabel { color: #8a91a8; font-size: 11px; font-weight: 700; letter-spacing: 1.2px; }
QLabel#statValue { color: #ffffff; font-size: 22px; font-weight: 750; }
QLabel#monogram { background-color: #7c5cff; color: #ffffff; border-radius: 10px; font-size: 16px; font-weight: 700; }
QLabel#avatar { background-color: #3a4057; color: #e7e9ee; border-radius: 17px; font-size: 15px; font-weight: 700; }
QLabel#cover { background-color: rgba(18, 20, 29, 0.62); border: 1px solid rgba(255, 255, 255, 0.09); border-radius: 14px; color: #4b5268; }
QLabel#ambient { background-color: transparent; }
QFrame#preview { background-color: rgba(24, 27, 37, 0.74); border: 1px solid rgba(139, 148, 175, 0.18); border-radius: 18px; }
QLabel#previewDot { color: #23a55a; font-size: 12px; }
QLabel#previewApp { color: #ffffff; font-size: 13px; font-weight: 700; }
QLabel#previewStatus { color: #8a91a8; font-size: 11px; font-weight: 600; }
QLabel#previewTitle { font-size: 14px; font-weight: 700; color: #ffffff; }
QLabel#previewState { color: #8a91a8; font-size: 12px; }
QLabel#previewTime { color: #5a6178; font-size: 11px; }
QLabel#previewButtons { color: #8b6cff; font-size: 11px; font-weight: 700; }
QTabWidget::pane { border: none; background: transparent; }
QTabBar { background: transparent; }
QTabBar::tab { background-color: rgba(23, 25, 35, 0.45); border: 1px solid rgba(139, 148, 175, 0.14); border-radius: 12px; padding: 8px 20px; margin: 2px 4px 2px 0; color: #9aa3bd; font-weight: 600; }
QTabBar::tab:selected { background-color: #7c5cff; border-color: #8b6cff; color: #ffffff; }
QTabBar::tab:hover:!selected { background-color: rgba(48, 54, 74, 0.85); color: #e7e9ee; }
QLabel#chipOk { color: #4ade80; background-color: rgba(74, 222, 128, 0.12); padding: 5px 12px; border-radius: 14px; font-weight: 700; font-size: 12px; }
QLabel#chipBad { color: #f87171; background-color: rgba(248, 113, 113, 0.12); padding: 5px 12px; border-radius: 14px; font-weight: 700; font-size: 12px; }
QFrame#card, QFrame#controls, QFrame#continueStrip { background-color: rgba(24, 27, 37, 0.7); border: 1px solid rgba(139, 148, 175, 0.17); border-radius: 18px; }
QFrame#sep { background-color: rgba(255, 255, 255, 0.07); border: none; max-height: 1px; }
QPushButton { background-color: #7c5cff; color: #ffffff; border: none; border-radius: 10px; padding: 7px 15px; font-weight: 700; }
QPushButton:hover { background-color: #8b6cff; }
QPushButton:pressed { background-color: #6a4ce0; }
QPushButton:disabled { background-color: rgba(38, 42, 55, 0.7); color: #5c6278; }
QPushButton#ghost { background-color: rgba(38, 42, 55, 0.66); color: #cbd0de; border: 1px solid rgba(139, 148, 175, 0.14); }
QPushButton#ghost:hover { background-color: rgba(48, 54, 74, 0.85); color: #ffffff; }
QPushButton#ghost[recording="true"] { background-color: rgba(124, 92, 255, 0.22); color: #ffffff; border-color: #8b6cff; }
QScrollArea#continueScroller { background: transparent; }
QFrame#continueTile { background-color: rgba(38, 42, 55, 0.6); border: 1px solid rgba(139, 148, 175, 0.12); border-radius: 10px; }
QFrame#continueTile:hover { background-color: rgba(48, 54, 74, 0.85); border-color: rgba(139, 148, 175, 0.28); }
QToolButton#continueTileButton { background: transparent; border: none; color: #e7e9ee; font-weight: 600; padding: 4px 6px 4px 12px; }
QToolButton#continueTileButton:hover { color: #ffffff; }
QToolButton#continueTileClose { background: transparent; border: none; color: #8a91a8; font-weight: 700; padding: 4px 10px; border-radius: 8px; font-size: 12px; }
QToolButton#continueTileClose:hover { color: #f87171; }
QCheckBox { spacing: 9px; color: #d6d9e3; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 5px; border: 1.5px solid #3a4057; background-color: rgba(23, 25, 35, 0.8); }
QCheckBox::indicator:hover { border-color: #8b6cff; }
QCheckBox::indicator:checked { background-color: #7c5cff; border-color: #7c5cff; }
QLineEdit, QSpinBox, QComboBox { background-color: rgba(16, 18, 26, 0.6); border: 1px solid rgba(139, 148, 175, 0.16); border-radius: 10px; padding: 6px 10px; color: #e7e9ee; selection-background-color: #7c5cff; selection-color: #ffffff; }
QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border-color: #8b6cff; }
QLineEdit { placeholder-text-color: #5c6278; }
QComboBox { padding-right: 24px; }
QComboBox::drop-down { border: none; background: transparent; width: 22px; border-top-right-radius: 10px; border-bottom-right-radius: 10px; }
QComboBox QAbstractItemView { background-color: rgba(23, 25, 35, 0.94); border: 1px solid rgba(139, 148, 175, 0.16); border-radius: 10px; padding: 4px; color: #e7e9ee; selection-background-color: rgba(124, 92, 255, 0.28); selection-color: #ffffff; }
QProgressBar { background-color: rgba(18, 20, 29, 0.6); border: none; border-radius: 4px; height: 8px; }
QProgressBar::chunk { background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #8b6cff, stop:1 #7c5cff); border-radius: 4px; }
QListWidget, QPlainTextEdit { background-color: rgba(18, 20, 29, 0.6); border: 1px solid rgba(139, 148, 175, 0.12); border-radius: 12px; }
QListWidget::item { padding: 7px 9px; border-radius: 7px; color: #cbd0de; }
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
QToolTip { background-color: rgba(23, 25, 35, 0.94); color: #e7e9ee; border: 1px solid rgba(139, 148, 175, 0.16); padding: 5px 8px; border-radius: 8px; }
"""

LIGHT_QSS = """
QWidget { background-color: #f4f5f7; color: #1c1e26; font-family: "Figtree", "Segoe UI Variable", "Segoe UI", sans-serif; font-size: 13px; }
QMainWindow { background-color: #f4f5f7; }
QLabel#appTitle { font-size: 18px; font-weight: 750; color: #111318; letter-spacing: 0.2px; }
QLabel#titleLabel { font-size: 26px; font-weight: 700; color: #111318; letter-spacing: -0.2px; }
QLabel#subLabel { color: #6b7280; font-size: 12px; }
QLabel#versionLabel { color: #9aa0b5; font-size: 10px; letter-spacing: 0.5px; }
QLabel#captionLabel { color: #6a49e8; font-size: 13px; font-weight: 700; letter-spacing: 0.3px; }
QLabel#sectionLabel { color: #6b7280; font-size: 11px; font-weight: 700; letter-spacing: 1.2px; }
QLabel#statValue { color: #111318; font-size: 22px; font-weight: 750; }
QLabel#monogram { background-color: #7c5cff; color: #ffffff; border-radius: 10px; font-size: 16px; font-weight: 700; }
QLabel#avatar { background-color: #c6cbd6; color: #1c1e26; border-radius: 17px; font-size: 15px; font-weight: 700; }
QLabel#cover { background-color: rgba(248, 249, 251, 0.7); border: 1px solid rgba(0, 0, 0, 0.08); border-radius: 14px; color: #9aa0b5; }
QLabel#ambient { background-color: transparent; }
QFrame#preview { background-color: rgba(255, 255, 255, 0.78); border: 1px solid rgba(0, 0, 0, 0.08); border-radius: 18px; }
QLabel#previewDot { color: #15803d; font-size: 12px; }
QLabel#previewApp { color: #111318; font-size: 13px; font-weight: 700; }
QLabel#previewStatus { color: #6b7280; font-size: 11px; font-weight: 700; }
QLabel#previewTitle { font-size: 14px; font-weight: 700; color: #111318; }
QLabel#previewState { color: #6b7280; font-size: 12px; }
QLabel#previewTime { color: #9aa0b5; font-size: 11px; }
QLabel#previewButtons { color: #6a49e8; font-size: 11px; font-weight: 700; }
QTabWidget::pane { border: none; background: transparent; }
QTabBar { background: transparent; }
QTabBar::tab { background-color: rgba(255, 255, 255, 0.55); border: 1px solid rgba(0, 0, 0, 0.07); border-radius: 12px; padding: 8px 20px; margin: 2px 4px 2px 0; color: #6b7280; font-weight: 600; }
QTabBar::tab:selected { background-color: #7c5cff; border-color: #8b6cff; color: #ffffff; }
QTabBar::tab:hover:!selected { background-color: rgba(236, 238, 243, 0.95); color: #111318; }
QLabel#chipOk { color: #15803d; background-color: rgba(22, 163, 74, 0.12); padding: 5px 12px; border-radius: 14px; font-weight: 700; font-size: 12px; }
QLabel#chipBad { color: #b91c1c; background-color: rgba(220, 38, 38, 0.12); padding: 5px 12px; border-radius: 14px; font-weight: 700; font-size: 12px; }
QFrame#card, QFrame#controls, QFrame#continueStrip { background-color: rgba(255, 255, 255, 0.8); border: 1px solid rgba(0, 0, 0, 0.08); border-radius: 18px; }
QFrame#sep { background-color: rgba(0, 0, 0, 0.08); border: none; max-height: 1px; }
QPushButton { background-color: #7c5cff; color: #ffffff; border: none; border-radius: 10px; padding: 7px 15px; font-weight: 700; }
QPushButton:hover { background-color: #8b6cff; }
QPushButton:pressed { background-color: #6a4ce0; }
QPushButton:disabled { background-color: rgba(236, 238, 243, 0.7); color: #9aa0b5; }
QPushButton#ghost { background-color: rgba(236, 238, 243, 0.78); color: #3b4252; border: 1px solid rgba(0, 0, 0, 0.07); }
QPushButton#ghost:hover { background-color: rgba(226, 229, 236, 0.95); color: #111318; }
QPushButton#ghost[recording="true"] { background-color: rgba(124, 92, 255, 0.22); color: #1c1e26; border-color: #7c5cff; }
QScrollArea#continueScroller { background: transparent; }
QFrame#continueTile { background-color: rgba(236, 238, 243, 0.78); border: 1px solid rgba(0, 0, 0, 0.07); border-radius: 10px; }
QFrame#continueTile:hover { background-color: rgba(226, 229, 236, 0.95); border-color: rgba(0, 0, 0, 0.14); }
QToolButton#continueTileButton { background: transparent; border: none; color: #1c1e26; font-weight: 600; padding: 4px 6px 4px 12px; }
QToolButton#continueTileButton:hover { color: #000000; }
QToolButton#continueTileClose { background: transparent; border: none; color: #6b7280; font-weight: 700; padding: 4px 10px; border-radius: 8px; font-size: 12px; }
QToolButton#continueTileClose:hover { color: #dc2626; }
QCheckBox { spacing: 9px; color: #374151; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 5px; border: 1.5px solid #c6cbd6; background-color: rgba(255, 255, 255, 0.8); }
QCheckBox::indicator:hover { border-color: #7c5cff; }
QCheckBox::indicator:checked { background-color: #7c5cff; border-color: #7c5cff; }
QLineEdit, QSpinBox, QComboBox { background-color: rgba(255, 255, 255, 0.85); border: 1px solid rgba(0, 0, 0, 0.1); border-radius: 10px; padding: 6px 10px; color: #1c1e26; selection-background-color: #7c5cff; selection-color: #ffffff; }
QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border-color: #7c5cff; }
QLineEdit { placeholder-text-color: #9aa0b5; }
QComboBox { padding-right: 24px; }
QComboBox::drop-down { border: none; background: transparent; width: 22px; border-top-right-radius: 10px; border-bottom-right-radius: 10px; }
QComboBox QAbstractItemView { background-color: rgba(255, 255, 255, 0.98); border: 1px solid rgba(0, 0, 0, 0.1); border-radius: 10px; padding: 4px; color: #1c1e26; selection-background-color: rgba(124, 92, 255, 0.18); selection-color: #111318; }
QProgressBar { background-color: rgba(236, 238, 243, 0.8); border: none; border-radius: 4px; height: 8px; }
QProgressBar::chunk { background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #8b6cff, stop:1 #7c5cff); border-radius: 4px; }
QListWidget, QPlainTextEdit { background-color: rgba(255, 255, 255, 0.8); border: 1px solid rgba(0, 0, 0, 0.08); border-radius: 12px; }
QListWidget::item { padding: 7px 9px; border-radius: 7px; color: #374151; }
QListWidget::item:hover { background-color: rgba(244, 245, 247, 0.95); }
QListWidget::item:selected { background-color: rgba(236, 238, 243, 0.95); color: #111318; }
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
QToolTip { background-color: rgba(255, 255, 255, 0.98); color: #1c1e26; border: 1px solid rgba(0, 0, 0, 0.1); padding: 5px 8px; border-radius: 8px; }
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
QFrame#card, QFrame#controls, QFrame#continueStrip { border-radius: 12px; }
QLineEdit, QSpinBox, QComboBox { padding: 4px 8px; }
QTabBar::tab { padding: 6px 16px; }
QFrame#continueTile { border-radius: 8px; }
QToolButton#continueTileButton { padding: 3px 4px 3px 8px; }
QToolButton#continueTileClose { padding: 3px 7px; }
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
    qss += """
    QListWidget#animeList { padding: 6px; outline: none; }
    QListWidget#animeList::item { padding: 0; margin: 0; border: none; border-radius: 8px; }
    QListWidget#animeList::item:focus { outline: none; border: none; }
    """
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


def _sample_accent_color(pixmap):
    """Pick a vibrant accent color from cover art, or '' when it can't.

    Downscales the art, then takes a saturation-weighted average so white/black
    filler and near-gray pixels don't dominate the result. The color is pushed
    to a mid lightness and a strong saturation so it pops on either theme.
    """
    if pixmap is None or pixmap.isNull():
        return ""
    img = pixmap.toImage().convertToFormat(QImage.Format_ARGB32)
    if img.isNull():
        return ""
    img = img.scaled(64, 64, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    r = g = b = wsum = 0.0
    for y in range(img.height()):
        for x in range(img.width()):
            c = QColor(img.pixel(x, y))
            if c.alpha() < 128:
                continue
            sat = c.hsvSaturation()
            w = (sat / 255.0) ** 2  # vivid pixels vote louder
            if w < 0.02:
                continue
            r += c.red() * w
            g += c.green() * w
            b += c.blue() * w
            wsum += w
    if wsum <= 0:
        return ""
    col = QColor(int(r / wsum), int(g / wsum), int(b / wsum))
    h = col.hue()
    if h < 0:  # achromatic average
        return ""
    s = col.saturation()
    v = col.value()
    s = max(160, min(255, int(s * 1.25)))
    v = 120 if v < 120 else (200 if v > 200 else v)
    return QColor.fromHsv(h, s, v).name()


def _ambient_box_colors(pixmap, boxes):
    """Average the cover art inside each normalized box.

    ``boxes`` is a list of ``((x0, y0, x1, y1), default)`` pairs where the
    coordinates are fractions of the (small) sampled image (0..1). Transparent
    pixels are skipped; the per-box default color is returned when a box is
    empty or the source is unusable.
    """
    img = pixmap.toImage().convertToFormat(QImage.Format_ARGB32)
    out = []
    if img.isNull():
        return [default for _box, default in boxes]
    img = img.scaled(24, 32, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    w, h = img.width(), img.height()
    for (fx0, fy0, fx1, fy1), default in boxes:
        x0, y0 = int(w * fx0), int(h * fy0)
        x1, y1 = max(x0 + 1, int(w * fx1)), max(y0 + 1, int(h * fy1))
        r = g = b = n = 0
        for y in range(y0, y1):
            for x in range(x0, x1):
                c = QColor(img.pixel(x, y))
                if c.alpha() < 128:
                    continue
                r += c.red()
                g += c.green()
                b += c.blue()
                n += 1
        out.append(QColor(r // n, g // n, b // n) if n else default)
    return out


def _ambient_gradient_colors(pixmap):
    """Dominant top and bottom edge tones for the vertical backdrop gradient."""
    return _ambient_box_colors(
        pixmap,
        [((0, 0, 1, 0.25), QColor(30, 32, 42)), ((0, 0.75, 1, 1), QColor(18, 19, 26))],
    )


def _ambient_diagonal_colors(pixmap):
    """Top-left and bottom-right corner tones for the diagonal backdrop gradient."""
    return _ambient_box_colors(
        pixmap,
        [((0, 0, 0.5, 0.5), QColor(30, 32, 42)), ((0.5, 0.5, 1, 1), QColor(18, 19, 26))],
    )


def _version_tuple(text):
    """Parse '1.2.3' into (1, 2, 3) for comparison, or None if malformed."""
    try:
        return tuple(int(p) for p in str(text or "").strip().split(".")[:3])
    except (ValueError, AttributeError):
        return None


def app_icon(size=64, accent="#7c5cff"):
    """Window/tray icon: the app logo, falling back to the "AP" monogram."""
    try:
        from ..logo import logo_icon
        icon = logo_icon(size)
        if not icon.isNull():
            return icon
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
        if self.property("modern"):
            return ""
        if self._alt_glyph and window is not self and window.isMaximized():
            return self._alt_glyph
        return self._glyph

    def enterEvent(self, event):  # noqa: N802 (Qt naming)
        self.setText(self._current_glyph())
        super().enterEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.property("modern"):
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(self.palette().buttonText().color(), 1.3))
        x, y = self.width() // 2, self.height() // 2
        if self.objectName() == "winClose":
            painter.drawLine(x-4, y-4, x+4, y+4)
            painter.drawLine(x-4, y+4, x+4, y-4)
        elif self.objectName() == "winMin":
            painter.drawLine(x-5, y, x+5, y)
        elif self.window().isMaximized():
            painter.drawRect(x-2, y-5, 7, 7)
            painter.fillRect(QRect(x-5, y-2, 7, 7), self.palette().window())
            painter.drawRect(x-5, y-2, 7, 7)
        else:
            painter.drawRect(x-4, y-4, 8, 8)

    def leaveEvent(self, event):  # noqa: N802 (Qt naming)
        self.setText(self._current_glyph() if self.property("modern") else "")
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
            if msg.message == 0x0312:  # WM_HOTKEY (global hotkeys)
                window = self._window()
                if window is not None:
                    window._on_global_hotkey(int(msg.wParam))
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


class _HotkeyButton(QPushButton):
    """A button that records the next key combination when clicked.

    Clicking it enters "recording" mode; the next key press (with at least one
    modifier) becomes the new hotkey spec, which is handed to *on_change*.
    Esc cancels. While recording, global hotkeys are unregistered so the combo
    the user is trying to assign doesn't fire (see *on_record_start/end*).
    """

    # Qt.Key_* -> Windows virtual-key code (subset used by RegisterHotKey).
    _QT_TO_VK = {}
    for _name in [c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"]:
        _QT_TO_VK[getattr(Qt, f"Key_{_name}")] = ord(_name)
    for _d in range(10):
        _QT_TO_VK[getattr(Qt, f"Key_{_d}")] = 0x30 + _d
    for _i in range(1, 13):
        _QT_TO_VK[getattr(Qt, f"Key_F{_i}")] = 0x6F + _i
    _QT_TO_VK.update({
        Qt.Key_Space: 0x20, Qt.Key_Return: 0x0D, Qt.Key_Enter: 0x0D,
        Qt.Key_Tab: 0x09, Qt.Key_Escape: 0x1B, Qt.Key_Up: 0x26,
        Qt.Key_Down: 0x28, Qt.Key_Left: 0x25, Qt.Key_Right: 0x27,
        Qt.Key_Home: 0x24, Qt.Key_End: 0x23, Qt.Key_PageUp: 0x21,
        Qt.Key_PageDown: 0x22, Qt.Key_Delete: 0x2E, Qt.Key_Insert: 0x2D,
    })

    def __init__(self, spec="", on_change=None, on_record_start=None,
                 on_record_end=None, parent=None):
        super().__init__(parent)
        self._on_change = on_change
        self._on_record_start = on_record_start
        self._on_record_end = on_record_end
        self._recording = False
        self.setObjectName("ghost")
        self.setToolTip(
            "Click, then press the new key combination. Esc cancels."
        )
        self.set_spec(spec)
        self.clicked.connect(self._begin_record)

    def set_spec(self, spec):
        self._spec = spec or ""
        if not self._recording:
            self.setText(self._spec or "Not set")

    def spec(self):
        return self._spec

    def _begin_record(self):
        if self._recording:
            self._end_record(cancel=True)
            return
        self._recording = True
        self.setText("Press keys… (Esc to cancel)")
        self.setProperty("recording", True)
        self.style().unpolish(self)
        self.style().polish(self)
        if self._on_record_start:
            self._on_record_start()

    def _end_record(self, cancel=False):
        self._recording = False
        self.setProperty("recording", False)
        self.style().unpolish(self)
        self.style().polish(self)
        if cancel:
            self.setText(self._spec or "Not set")
        if self._on_record_end:
            self._on_record_end()

    def keyPressEvent(self, event):
        if not self._recording:
            return super().keyPressEvent(event)
        if event.key() == Qt.Key_Escape:
            self._end_record(cancel=True)
            event.accept()
            return
        from ..core.hotkeys import MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, format_hotkey
        modifiers = event.modifiers()
        mods = 0
        if modifiers & Qt.ControlModifier:
            mods |= MOD_CONTROL
        if modifiers & Qt.AltModifier:
            mods |= MOD_ALT
        if modifiers & Qt.ShiftModifier:
            mods |= MOD_SHIFT
        if modifiers & Qt.MetaModifier:
            mods |= MOD_WIN
        vk = self._QT_TO_VK.get(event.key())
        if vk is None or mods == 0:
            event.accept()  # no key or no modifier yet; keep recording
            return
        spec = format_hotkey(mods, vk)
        if not spec:
            event.accept()
            return
        self._spec = spec
        self._end_record()
        self.setText(spec)
        if self._on_change:
            self._on_change(spec)
        event.accept()


class MainWindow(QMainWindow):
    def __init__(self, cfg: ConfigManager, bridge: UiBridge, controller: Controller):
        super().__init__()
        self.cfg = cfg
        self.bridge = bridge
        self.controller = controller
        from ..core.activity import ActivityJournal
        self._activity = ActivityJournal(APP_DIR / "activity.json")
        self._activity_playback_key = None
        self._banner_url = ""
        self._banner_requests = set()
        self._login_flow = None
        self._login_generation = 0
        self._update_busy = False
        self._update_state = {'stage': 'idle', 'message': 'Updates have not been checked yet.'}

        self._current_payload = None
        self._cover_url = ""
        self._cover_requested = False
        self._cover_pixmap = None
        self._ambient = None          # cinematic backdrop QLabel (full-window)
        self._ambient_src_version = 0  # bumps whenever the backdrop source changes
        self._ambient_cache_key = None
        self._left_pane = None
        self._splitter = None
        self._tray = None
        self._quitting = False
        self._prev_chrome_ok = None
        self._prev_discord_ok = None
        self._last_chrome_status = ""
        self._tray_base_pm = None     # clean logo pixmap for progress-ring overlays
        self._tray_fraction = None    # playback progress drawn as a ring on the icon
        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(1000)
        self._preview_timer.timeout.connect(self._tick_preview_elapsed)
        self._preview_timer.start()
        self._nam = QNetworkAccessManager(self)
        self._nam.finished.connect(self._on_cover_reply)
        self._cover_cache_dir = APP_DIR / "covers"
        self._cover_paths = {}
        self._artwork = self.controller.artwork
        self._artwork.configure(self.cfg.data.tmdb_credential, self.cfg.data.cover_source)
        self._artwork_timer = QTimer(self)
        self._artwork_timer.setSingleShot(True)
        self._artwork_timer.setInterval(600)
        self._artwork_timer.timeout.connect(self._refresh_artwork_views)
        from .image_cache import ImageCache
        from .scrolling import ScrollController
        self._images = ImageCache(self)
        self._image_repaint_timer=QTimer(self)
        self._image_repaint_timer.setSingleShot(True);self._image_repaint_timer.setInterval(32)
        self._image_repaint_timer.timeout.connect(self._repaint_artwork)
        self._images.ready.connect(self._queue_artwork_repaint)
        self._scrolling = ScrollController(self)
        self._recent_icon_pending = {}   # cover-url -> QListWidgetItem waiting for its icon
        self._recent_icon_fetching = set()  # cover-urls with a download in flight
        self._stats_icon_pending = {}    # cover-url -> QListWidgetItem (stats list)
        self._stats_icon_fetching = set()
        self._section_icon_pending = {}  # cover-url -> QListWidgetItem (AniList sections)
        self._section_icon_fetching = set()
        self._library_icon_pending = {}  # cover-url -> QListWidgetItem (Library tab)
        self._library_icon_fetching = set()
        self._poster_fetching = set()
        self._poster_failed = set()
        self._notified_airing = set()  # (anilist_id, episode) pairs already announced
        self._tunnel_proc = None      # cloudflared subprocess for the share tunnel
        self._match_icon_pending = {}  # cover-url -> QToolButton (match chooser)
        self._match_icon_fetching = set()
        self._match_panel_url = None   # page URL the match chooser is pinned to
        self._continue_icon_pending = {}  # cover-url -> QToolButton (continue strip)
        self._continue_icon_fetching = set()
        self._title_drag = None
        # Native minimize/restore/maximize animations (see _animated_minimize
        # and _apply_native_window_styles): the caption frame bits are applied
        # on first show so the DWM plays the real OS animations.
        self._fade_anim = None
        self._closing_fade = False       # close button started the fade-out
        self._native_styles_applied = False  # showEvent ran the style patch once
        # Header elements hidden at narrow widths (see _update_header_for_width).
        self._header_hid_chips = False
        self._header_hid_sub = False
        # Borderless window: we draw our own macOS-style title bar + traffic lights.
        # WindowMinimizeButtonHint keeps WS_MINIMIZEBOX so the Windows taskbar
        # button can still minimize the window (click taskbar icon to toggle).
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowMinimizeButtonHint)

        self.setWindowTitle(f"AniPresence {__version__}")
        self.resize(1280, 860)
        self.setMinimumSize(600, 430)
        self.setWindowIcon(app_icon(64, cfg.data.accent))
        self._applied_theme = resolve_theme(cfg.data.theme)
        self._start_maximized = False
        self._system_refresh = QTimer(self)
        self._system_refresh.setInterval(10000)
        self._system_refresh.timeout.connect(self._maybe_resync_system_theme)
        self._build_ui()
        from PySide6.QtGui import QShortcut, QKeySequence
        self._search_shortcut = QShortcut(QKeySequence("Ctrl+K"),self)
        self._search_shortcut.activated.connect(self._open_quick_search)
        self._sidebar_shortcut = QShortcut(QKeySequence("Ctrl+B"),self)
        self._sidebar_shortcut.activated.connect(self._toggle_sidebar)
        self._connect_signals()
        self._scrolling.install()
        self._populate_anilist_accounts()
        self._load_toggles_from_config()
        self._build_tray()
        self.show_placeholder()
        self._apply_stylesheet()
        self._install_resize_filter()
        self._restore_window_state()
        self._system_refresh.start()
        self._diagnostics_timer = QTimer(self)
        self._diagnostics_timer.setInterval(3000)
        self._diagnostics_timer.timeout.connect(self._refresh_extension_diagnostics)
        self._diagnostics_timer.start()
        self._update_timer = QTimer(self)
        self._update_timer.setInterval(6 * 60 * 60 * 1000)
        self._update_timer.timeout.connect(self._auto_check_updates)
        self._update_timer.start()
        self._startup_update_timer = QTimer(self)
        self._startup_update_timer.setSingleShot(True)
        self._startup_update_timer.timeout.connect(self._auto_check_updates)
        self._startup_update_timer.start(0)

    # ----------------------------------------------------------------- tray
    def _build_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self._tray_base_pm = None  # re-derive under the current accent
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
        if self._login_flow:
            self._login_flow.cancel()
        self._quitting = True
        self.close()

    def _toggle_max(self):
        """Maximize/restore through the OS so the native Windows animation plays."""
        try:
            user32 = _declare_user32()
            hwnd = int(self.winId())
            if self.isMaximized():
                user32.ShowWindow(hwnd, SW_RESTORE)
            else:
                user32.ShowWindow(hwnd, SW_MAXIMIZE)
        except Exception:  # noqa: BLE001 - fall back to Qt's own maximize
            if self.isMaximized():
                self.showNormal()
            else:
                self.showMaximized()

    def _animated_minimize(self):
        """Minimize with the native Windows 11 fly-to-taskbar animation.

        The caption frame bits re-added in ``_apply_native_window_styles`` make
        the DWM animate this window into the taskbar exactly like every other
        app on Windows 11 - no custom animation needed.
        """
        self.showMinimized()

    def _animated_restore(self):
        """Restore with the native Windows 11 fly-back animation."""
        try:
            user32 = _declare_user32()
            hwnd = int(self.winId())
            if self.isMinimized():
                user32.ShowWindow(hwnd, SW_RESTORE)
        except Exception:  # noqa: BLE001
            pass
        self.show()
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------ DWM native
    def _apply_native_window_styles(self):
        """Re-add the caption frame bits so the DWM plays real OS animations."""
        try:
            user32 = _declare_user32()
            hwnd = int(self.winId())
            style = user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
            user32.SetWindowLongPtrW(hwnd, GWL_STYLE, style | _NATIVE_STYLES)
            user32.SetWindowPos(
                hwnd, None, 0, 0, 0, 0,
                SWP_FRAMECHANGED | SWP_NOSIZE | SWP_NOMOVE,
            )
        except Exception:  # noqa: BLE001 - non-Windows keeps the plain frameless window
            pass

    def nativeEvent(self, eventType, message):  # noqa: N802 (Qt naming)
        """Hide the OS caption and keep the maximized client in the work area."""
        try:
            msg = wintypes.MSG.from_address(int(message))
        except Exception:  # noqa: BLE001
            return super().nativeEvent(eventType, message)
        if msg.message == WM_NCCALCSIZE:
            try:
                user32 = _declare_user32()
                if user32.IsZoomed(int(msg.hWnd)):
                    params = _NCCALCSIZE_PARAMS.from_address(int(msg.lParam))
                    monitor = _MONITORINFO()
                    monitor.cbSize = ctypes.sizeof(_MONITORINFO)
                    user32.GetMonitorInfoW(
                        user32.MonitorFromWindow(int(msg.hWnd), 2),
                        ctypes.byref(monitor),
                    )
                    params.rgrc0 = monitor.rcWork
                    return True, WVR_REDRAW if msg.wParam else 0
                return False, 0
            except Exception:  # noqa: BLE001 - never break the native event chain
                return False, 0
        return super().nativeEvent(eventType, message)

    def changeEvent(self, event):  # noqa: N802 (Qt naming)
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.WindowStateChange:
            btn = self.win_max_btn
            if btn.underMouse():
                btn.setText(btn._current_glyph())
        super().changeEvent(event)

    def _update_header_for_width(self):
        """Tuck header elements away on narrow windows so nothing gets clipped."""
        w = self.width()
        if hasattr(self,"quick_search_button"):
            self.quick_search_button.setText("Search" if w<760 else "Search · Ctrl+K")
            self.quick_search_button.setToolTip("Search library, history and AniList · Ctrl+K")
        if hasattr(self, "_sidebar"):
            narrow = w < 900
            self._sidebar.setFixedWidth(68 if narrow else 220)
            for button, name in zip(self._nav_buttons, self._page_names):
                button.setText("" if narrow else name)
            self._reflow_controls()
            self._update_layout_spacing()
        if w < 820 and not self._header_hid_chips:
            self.chrome_chip.hide()
            self.discord_chip.hide()
            self._header_hid_chips = True
        elif w >= 820 and self._header_hid_chips:
            self.chrome_chip.show()
            self.discord_chip.show()
            self._header_hid_chips = False
        self._sidebar_account.setVisible(self.cfg.data.ui_layout != "classic")
        if hasattr(self,"_cinema_nav_buttons"):
            self._sync_navigation(self.tabbar.currentIndex())
        if w < 900:
            self._sidebar_account.setText("")
        else:
            active = self.controller.active_anilist_account
            self._sidebar_account.setText(f"{active or 'Default profile'}\nAniList · Accounts")

    def _install_resize_filter(self):
        """Enable native edge-resize for the borderless window (Windows only)."""
        if not sys.platform.startswith("win"):
            return
        self._resize_filter = _EdgeResizeFilter(lambda: self)
        QApplication.instance().installNativeEventFilter(self._resize_filter)
        self._register_hotkey()

    _HOTKEY_ID = 0xAF01          # toggle window
    _HOTKEY_PAUSE_ID = 0xAF02    # toggle detection
    _HOTKEY_CLEAR_ID = 0xAF03    # clear presence
    _HOTKEY_NEXT_ID = 0xAF04     # next episode
    # hotkey_id -> config field holding its spec string.
    _HOTKEY_CONFIG = {
        _HOTKEY_ID: "hotkey_toggle",
        _HOTKEY_PAUSE_ID: "hotkey_pause",
        _HOTKEY_CLEAR_ID: "hotkey_clear",
        _HOTKEY_NEXT_ID: "hotkey_next",
    }
    _HOTKEY_LABELS = {
        _HOTKEY_ID: "Show / hide window",
        _HOTKEY_PAUSE_ID: "Pause / resume detection",
        _HOTKEY_CLEAR_ID: "Clear presence",
        _HOTKEY_NEXT_ID: "Next episode",
    }

    def _register_hotkey(self):
        """Register every configured global hotkey (Windows only).

        Specs are read from config, so hotkeys changed in Settings apply live.
        """
        self._hotkey_registered = {}
        try:
            import ctypes
            from ..core.hotkeys import parse_hotkey
            for hotkey_id, field in self._HOTKEY_CONFIG.items():
                parsed = parse_hotkey(getattr(self.cfg.data, field, ""))
                if parsed is None:
                    self._hotkey_registered[hotkey_id] = False
                    continue
                modifiers, vk = parsed
                ok = bool(
                    ctypes.windll.user32.RegisterHotKey(
                        int(self.winId()), hotkey_id, modifiers, vk
                    )
                )
                self._hotkey_registered[hotkey_id] = ok
        except (OSError, AttributeError, ValueError):
            self._hotkey_registered = {}

    def _reload_hotkeys(self):
        """Unregister and re-register after a Settings change."""
        self._unregister_hotkey()
        self._register_hotkey()

    def _unregister_hotkey(self):
        registered = getattr(self, "_hotkey_registered", None)
        if not registered:
            return
        try:
            import ctypes
            for hotkey_id, ok in registered.items():
                if ok:
                    ctypes.windll.user32.UnregisterHotKey(int(self.winId()), hotkey_id)
        except (OSError, AttributeError, ValueError):
            pass
        self._hotkey_registered = {}

    def _on_global_hotkey(self, hotkey_id):
        """Dispatch a global-hotkey event by its registered id."""
        if hotkey_id == self._HOTKEY_ID:
            self._toggle_visible()
        elif hotkey_id == self._HOTKEY_PAUSE_ID:
            self._toggle_detection()
        elif hotkey_id == self._HOTKEY_CLEAR_ID:
            self.controller.clear_rpc()
            self._notify("AniPresence", "Presence cleared.")
        elif hotkey_id == self._HOTKEY_NEXT_ID and self._current_payload:
            self._next_episode()

    def _toggle_detection(self):
        """Flip the auto-detection toggle (used by the pause hotkey)."""
        state = not self.cfg.data.detection_enabled
        self.cfg.set(detection_enabled=state)
        self.detection_check.setChecked(state)
        self._notify(
            "AniPresence",
            "Auto-detection paused." if not state else "Auto-detection resumed.",
        )

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
        from .effects import GlassBackground
        central = GlassBackground()
        central.configure(self.cfg.data.glass_effects, self._applied_theme, self.cfg.data.accent)
        self._layout_mode = None
        self._control_grid_mode = None
        self._stats_grid_columns = None
        outer = QVBoxLayout(central)
        self._outer_layout = outer
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.setCentralWidget(central)

        # Cinematic backdrop: the cover art blurred into a frosted full-window
        # wall that sits behind everything (title bar included). It's only
        # visible when the base window fills are translucent (see
        # _apply_stylesheet) and a cover is loaded.
        self._ambient = QLabel(central)
        self._ambient.setObjectName("ambient")
        self._ambient.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._ambient_effect = QGraphicsOpacityEffect(self._ambient)
        self._ambient_effect.setOpacity(
            0.6 if self._applied_theme == "light" else 0.45
        )
        self._ambient.setGraphicsEffect(self._ambient_effect)
        self._ambient.hide()
        self._ambient.lower()

        # Title bar: mac-style traffic lights + draggable header.
        self.title_bar = _ChromeTitleBar()
        self.title_bar.setObjectName("titlebar")
        header = QHBoxLayout(self.title_bar)
        self._header_layout = header
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

        from ..logo import OrbitWordmark
        self._brand = OrbitWordmark(compact_icon=True)
        self._brand.setToolTip(f"AniPresence {__version__} · About")
        self._brand.setCursor(Qt.PointingHandCursor)
        self._brand.mousePressEvent = lambda _event: self._on_about()
        header.addWidget(self._brand, 1)
        header.addStretch(1)
        self.sidebar_toggle = QPushButton("Sidebar")
        self.sidebar_toggle.setObjectName("ghost");self.sidebar_toggle.setCheckable(True)
        self.sidebar_toggle.clicked.connect(self._toggle_sidebar)
        header.addWidget(self.sidebar_toggle)
        self.quick_search_button = QPushButton("Search · Ctrl+K")
        self.quick_search_button.setObjectName("ghost")
        self.quick_search_button.clicked.connect(self._open_quick_search)
        header.addWidget(self.quick_search_button)

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
        self.theme_combo.addItems(["Dark", "Light", "System", "OLED"])
        self.theme_combo.setToolTip("Interface theme. 'System' follows Windows light/dark mode.")
        self.theme_combo.setFixedWidth(88)
        header.addWidget(self.theme_combo)
        self.accent_combo = QComboBox()
        for name, hexval in ACCENT_PRESETS:
            self.accent_combo.addItem(name, hexval)
        self.accent_combo.addItem("Custom color…", CUSTOM_ACCENT)
        self.accent_combo.setToolTip(
            "Accent preset, or 'Custom color…' to pick any color. "
            "Auto accent (in Settings) overrides this while enabled."
        )
        self.accent_combo.setFixedWidth(150)
        header.addWidget(self.accent_combo)
        header.addSpacing(6)
        for btn in (self.win_min_btn, self.win_max_btn, self.win_close_btn):
            header.addWidget(btn)
        # Keep the title bar pinned above the scrollable body.
        outer.insertWidget(0, self.title_bar)
        self._update_banner = QFrame()
        self._update_banner.setObjectName("updateBanner")
        banner_row = QHBoxLayout(self._update_banner)
        banner_row.setContentsMargins(18, 10, 18, 10)
        self._update_banner_label = QLabel()
        self._update_banner_label.setWordWrap(True)
        banner_row.addWidget(self._update_banner_label, 1)
        view_update = QPushButton("View update")
        view_update.clicked.connect(self._show_update_settings)
        banner_row.addWidget(view_update)
        outer.addWidget(self._update_banner)
        self._update_banner.hide()

        # Chrome-style top-level tabs: the main dashboard, recently-watched and
        # the log each get their own tab; switching tabs swaps the whole page.
        self.tabbar = QTabBar()
        self.tabbar.setDrawBase(False)
        self._tabs_host = QWidget()
        tab_row = QHBoxLayout(self._tabs_host)
        tab_row.setContentsMargins(0, 6, 0, 0)
        tab_row.setSpacing(0)
        tab_row.addStretch(1)
        tab_row.addWidget(self.tabbar)
        tab_row.addStretch(1)
        outer.addWidget(self._tabs_host)
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

        from .browse import BrowsePanel
        self._browse = BrowsePanel()
        self.stack.addWidget(self._browse)

        recent_page = QWidget()
        recent_wrapper = QVBoxLayout(recent_page)
        self._history_classic = QWidget()
        recent_layout = QVBoxLayout(self._history_classic)
        recent_wrapper.addWidget(self._history_classic)
        self._history_modern = HistoryPanel(self._cover_cache_path)
        self._history_modern.activated.connect(self._open_anime)
        self._history_modern.clear_requested.connect(self._on_clear_history)
        self._history_modern.refresh_requested.connect(self._refresh_history_feed)
        self._history_modern.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._history_modern.list.customContextMenuRequested.connect(
            lambda pos: self._recent_menu(pos, self._history_modern.list))
        recent_wrapper.addWidget(self._history_modern,1)
        recent_layout.setContentsMargins(m, (6 if not compact else 4), m, m)
        recent_layout.setSpacing(self._gap)
        recent_header = QHBoxLayout()
        recent_header.setSpacing(8)
        recent_header.addWidget(self._field_label("Recently watched"))
        recent_header.addStretch(1)
        self._history_refresh = QPushButton("Refresh AniList")
        self._history_refresh.setObjectName("ghost")
        self._history_refresh.clicked.connect(self._refresh_history_feed)
        recent_header.addWidget(self._history_refresh)
        clear_history_btn = QPushButton("Clear history")
        clear_history_btn.setObjectName("ghost")
        clear_history_btn.setToolTip("Remove every entry from the watch history.")
        clear_history_btn.clicked.connect(self._on_clear_history)
        recent_header.addWidget(clear_history_btn)
        recent_layout.addLayout(recent_header)
        self.recent_list = AnimeList()
        self.recent_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.recent_list.setIconSize(QSize(38, 53))
        recent_layout.addWidget(self.recent_list, 1)
        self.stack.addWidget(recent_page)

        self._build_library_page(m, compact)
        self.stack.addWidget(self._library_page)

        self._build_stats_page(m, compact)
        self._stats_classic = self._stats_page.takeWidget()
        stats_wrapper = QWidget()
        stats_layout = QVBoxLayout(stats_wrapper)
        self._analytics = AnalyticsPanel(self.controller, self._cover_cache_path)
        self._analytics.view_all.connect(self._show_all_stats)
        stats_layout.addWidget(self._analytics)
        stats_layout.addWidget(self._stats_classic)
        self._stats_page.setWidget(stats_wrapper)
        self.stack.addWidget(self._stats_page)

        self._build_settings_page(m, compact)
        self.stack.addWidget(self._settings_page)

        log_page = QWidget()
        log_wrapper = QVBoxLayout(log_page)
        self._log_classic = QWidget()
        log_layout = QVBoxLayout(self._log_classic)
        log_wrapper.addWidget(self._log_classic)
        self._activity_panel = ActivityPanel(self._activity)
        self._activity_panel.clear_requested.connect(self._on_clear_log)
        log_wrapper.addWidget(self._activity_panel,1)
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
        from .details import DetailsPage
        self._details = DetailsPage()
        self.stack.addWidget(self._details)
        self._details_v2 = self._details
        from .cinema import Overview, History
        from .cinema_stats import Statistics
        from .cinema_details import CinemaDetails
        self._cinema_overview = Overview()
        self._cinema_history = History()
        self._cinema_stats = Statistics()
        self._details_v3 = CinemaDetails()
        for page in (self._cinema_overview, self._cinema_history, self._cinema_stats, self._details_v3):
            self.stack.addWidget(page)

        self.tabbar.addTab("Main")
        self.tabbar.addTab("Browse")
        self._browse_tab_index = self.tabbar.count() - 1
        self.tabbar.addTab("Recently Watched")
        self._history_tab_index = self.tabbar.count() - 1
        self.tabbar.addTab("Library")
        self._library_tab_index = self.tabbar.count() - 1
        self.tabbar.addTab("Stats")
        self._stats_tab_index = self.tabbar.count() - 1
        self.tabbar.addTab("Settings")
        self.tabbar.addTab("Log")
        self._log_tab_index = self.tabbar.count() - 1
        self._settings_tab_index = self.tabbar.count() - 2
        self.tabbar.currentChanged.connect(self._on_tab_changed)
        self.tabbar.tabBarClicked.connect(lambda index: self._on_tab_changed(index) if self.stack.currentWidget() is self._details else None)
        self._build_navigation_shell(outer)

        left = QHBoxLayout()          # dashboard row: left column | controls
        self._dashboard_row = left
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(self._gap)
        dash_root = QVBoxLayout(dashboard)
        self._dashboard_layout = dash_root
        dash_root.setContentsMargins(0, 0, 0, 0)
        dash_root.setSpacing(self._gap)
        dash_root.addLayout(left, 1)

        left_col = QVBoxLayout()      # card on top, live presence preview below
        self._now_column = left_col
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.setSpacing(self._gap)
        left.addLayout(left_col, 5)

        card = QFrame()
        card.setObjectName("card")
        card.setMinimumWidth(420)
        self._left_pane = card
        card_layout = QVBoxLayout(card)
        self._now_card_layout = card_layout
        card_layout.setContentsMargins(self._pad, self._pad, self._pad, self._pad)
        card_layout.setSpacing(14)

        # Top row: cover on the left, every action button to its right.
        top_row = QHBoxLayout()
        self._now_top_row = top_row
        top_row.setSpacing(18)

        from .poster import RoundedPoster
        self.cover_label = RoundedPoster()
        self.cover_label.setObjectName("cover")
        self.cover_label.setFixedSize(150, 210)
        self.cover_label.setAlignment(Qt.AlignCenter)
        top_row.addWidget(self.cover_label, 0, Qt.AlignTop)
        self._cover_spinner = _Spinner()
        self._cover_spinner.setParent(self.cover_label)
        self._cover_spinner.set_color(self.cfg.data.accent)
        self._cover_spinner.hide()
        self._place_cover_spinner()

        self._classic_actions = QWidget()
        self._classic_actions.setObjectName("surfaceContent")
        button_col = QVBoxLayout(self._classic_actions)
        button_col.setContentsMargins(0, 0, 0, 0)
        self._classic_action_layout = button_col
        button_col.setSpacing(8)
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
        self.share_now_btn = QPushButton("Share now playing")
        self.share_now_btn.setObjectName("ghost")
        self.share_now_btn.setToolTip(
            "Open the public Now Playing page for this machine (localhost).\n"
            "Use 'Share via tunnel' below to expose it on the internet."
        )
        self.share_tunnel_btn = QPushButton("Share via tunnel")
        self.share_tunnel_btn.setObjectName("ghost")
        self.share_tunnel_btn.setToolTip(
            "Expose the Now Playing page through a free cloudflared tunnel and "
            "copy the public link. Requires the cloudflared binary."
        )
        for _btn in (self.open_site_btn, self.open_pwa_btn, self.open_anilist_btn,
                     self.fix_match_btn, self.next_ep_btn, self.copy_url_btn,
                     self.share_now_btn, self.share_tunnel_btn):
            _btn.setMinimumWidth(132)
            button_col.addWidget(_btn)
        button_col.addStretch(1)
        top_row.addWidget(self._classic_actions, 0)
        card_layout.addLayout(top_row)

        # Bottom section: title + playback bar, spanning the full card width.
        self._now_info = QWidget()
        self._now_info.setObjectName("surfaceContent")
        self._now_info.setMinimumWidth(0)
        info = QVBoxLayout(self._now_info)
        info.setContentsMargins(0, 0, 0, 0)
        self._now_info_layout = info
        info.setSpacing(7)
        self.episode_label = QLabel("")
        self.episode_label.setWordWrap(True)
        self.episode_label.setObjectName("captionLabel")
        info.addWidget(self.episode_label)

        self.anime_title = QLabel("No anime detected")
        self.anime_title.setObjectName("titleLabel")
        self.anime_title.setWordWrap(True)
        info.addWidget(self.anime_title)
        from .details import Artwork
        self.anime_logo = Artwork(fit=True)
        self.anime_logo.setFixedHeight(95)
        self.anime_logo.hide()
        info.addWidget(self.anime_logo)

        self.match_label = QLabel("")
        self.match_label.setObjectName("subLabel")
        self.match_label.setWordWrap(True)
        info.addWidget(self.match_label)

        self.match_chooser = QWidget()
        chooser_lay = QVBoxLayout(self.match_chooser)
        chooser_lay.setContentsMargins(0, 0, 0, 0)
        chooser_lay.setSpacing(6)
        self.match_chooser_label = QLabel("Did you mean?")
        self.match_chooser_label.setObjectName("sectionLabel")
        chooser_lay.addWidget(self.match_chooser_label)
        self.match_chooser_row = QHBoxLayout()
        self.match_chooser_row.setSpacing(6)
        chooser_lay.addLayout(self.match_chooser_row)
        self.match_chooser_actions = QHBoxLayout()
        self.match_chooser_actions.setSpacing(6)
        chooser_lay.addLayout(self.match_chooser_actions)
        self.match_chooser.hide()
        info.addWidget(self.match_chooser)

        info.addSpacing(6)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self._progress_animation = QPropertyAnimation(self.progress, b"value", self.progress)
        self._progress_animation.setDuration(320)
        self._progress_animation.setEasingCurve(QEasingCurve.OutCubic)
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

        card_layout.addWidget(self._now_info)
        self._modern_actions = QWidget()
        self._modern_actions.setObjectName("surfaceContent")
        self._modern_actions.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self._modern_action_row = QHBoxLayout(self._modern_actions)
        self._modern_action_row.setContentsMargins(0, 8, 0, 0)
        self._more_actions = QPushButton("More actions")
        self._more_actions.setObjectName("ghost")
        self._more_menu = QMenu(self._more_actions)
        self._more_links = []
        for button in (self.open_pwa_btn, self.open_anilist_btn, self.fix_match_btn,
                       self.copy_url_btn, self.share_now_btn, self.share_tunnel_btn):
            action = self._more_menu.addAction(button.text())
            action.triggered.connect(lambda checked=False, b=button: b.click())
            self._more_links.append((action, button))
        self._more_menu.aboutToShow.connect(self._sync_more_actions)
        self._more_actions.setMenu(self._more_menu)
        self._modern_action_row.addStretch(1)
        self._modern_action_row.addWidget(self._more_actions)
        card_layout.addWidget(self._modern_actions)
        self._modern_actions.hide()
        left_col.addWidget(card, 5)

        # Live mockup of the Discord activity card, updated in real time.
        self._build_preview(left_col)

        # Controls
        controls = QFrame()
        self._controls_card = controls
        controls.setObjectName("controls")
        c_layout = QVBoxLayout(controls)
        self._classic_controls_layout = c_layout
        c_layout.setContentsMargins(self._pad, (12 if not self.cfg.data.compact else 8), self._pad, self._pad)
        c_layout.setSpacing(10)
        c_layout.addWidget(self._field_label("CONTROLS"))

        self.rpc_check = Toggle("Enable Discord RPC")
        self.detection_check = Toggle("Enable auto detection")
        self.auto_chrome_check = Toggle("Auto-launch browser")
        self.tray_check = Toggle("Minimize to tray on close")
        self.startup_check = Toggle("Start with Windows")
        self.privacy_check = Toggle("Privacy mode (hide URLs in log)")
        self.compact_check = Toggle("Compact layout")
        self.idle_check = Toggle("Idle presence when nothing playing")
        self.idle_check.setToolTip("Show a 'Browsing anime' presence instead of clearing it when no video is detected.")
        self.mica_check = Toggle("Mica backdrop (Windows 11)")
        self.mica_check.setToolTip("Draw the Mica material behind the window (applies on next start; falls back to acrylic).")
        self.cinematic_check = Toggle("Cinematic backdrop")
        self.cinematic_check.setToolTip(
            "Dim the current cover art behind the whole window for a cinematic look. "
            "The base window fills become translucent so the backdrop shows through."
        )
        self.auto_accent_check = Toggle("Auto accent from cover art")
        self.auto_accent_check.setToolTip(
            "Sample a vibrant accent color from the current cover art and tint the "
            "buttons, spinner and tray icon per show. Pick a manual accent to override."
        )
        self.hide_adult_check = Toggle("Hide 18+/ecchi from Discord status")
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
            self.cinematic_check, self.hide_adult_check,
            self.auto_accent_check,
        ]
        self._control_boxes = boxes
        grid = QGridLayout()
        self._controls_grid = grid
        grid.setSpacing(8)
        for i, cb in enumerate(boxes):
            grid.addWidget(cb, i // 2, i % 2)
        c_layout.addLayout(grid)

        style_row = QHBoxLayout()
        style_row.setSpacing(8)
        style_label = QLabel("Backdrop style")
        style_label.setObjectName("sectionLabel")
        style_row.addWidget(style_label)
        self.cinematic_style_combo = QComboBox()
        self.cinematic_style_combo.addItem("Frosted blur", "frosted")
        self.cinematic_style_combo.addItem("Color gradient", "gradient")
        self.cinematic_style_combo.addItem("Diagonal gradient", "diagonal")
        self.cinematic_style_combo.addItem("Dreamy blur", "dreamy")
        self.cinematic_style_combo.addItem("Spotlight vignette", "vignette")
        self.cinematic_style_combo.addItem("Monochrome tint", "duotone")
        self.cinematic_style_combo.setToolTip(
            "How the cinematic backdrop is drawn from the cover art: frosted or "
            "very heavy (dreamy) blur, vertical or diagonal color gradients, a "
            "blur with a dark spotlight vignette, or a monochrome version tinted "
            "with the accent color. Any of them fills the whole window."
        )
        self.cinematic_style_combo.currentIndexChanged.connect(
            self._on_cinematic_style_changed
        )
        style_row.addWidget(self.cinematic_style_combo)
        style_row.addStretch(1)
        c_layout.addLayout(style_row)

        c_layout.addWidget(self._sep())

        self._account_card = QFrame()
        self._account_card.setObjectName("card")
        account_layout = QVBoxLayout(self._account_card)
        account_layout.setContentsMargins(self._pad,self._pad,self._pad,self._pad)
        account_layout.addWidget(self._field_label("ANILIST ACCOUNT"))
        c_layout.addWidget(self._account_card)
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
        account_layout.addLayout(account_row)
        account_hint = QLabel(
            "Each account's watch history loads from its own save file on switch."
        )
        account_hint.setObjectName("subLabel")
        account_hint.setWordWrap(True)
        account_layout.addWidget(account_hint)
        c_layout.addWidget(self._sep())

        self._actions_card = QFrame()
        self._actions_card.setObjectName("card")
        actions_layout = QVBoxLayout(self._actions_card)
        actions_layout.setContentsMargins(self._pad,self._pad,self._pad,self._pad)
        c_layout.addWidget(self._actions_card)
        actions_layout.addWidget(self._field_label("CONNECTION TOOLS"))
        self.reconnect_btn = QPushButton("Reconnect Discord")
        self.clear_rpc_btn = QPushButton("Clear RPC")
        self.backup_btn = QPushButton("Backup config")
        self.backup_btn.setObjectName("ghost")
        self.backup_btn.setToolTip("Copy config.json to the Desktop")
        self.clear_rpc_btn.setObjectName("ghost")
        self.reconnect_btn.setObjectName("ghost")
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        action_row.addWidget(self.reconnect_btn)
        action_row.addWidget(self.clear_rpc_btn)
        actions_layout.addLayout(action_row)
        actions_layout.addWidget(self.backup_btn)

        c_layout.addStretch(1)
        left.addWidget(controls, 4)

        # "Continue watching": recent titles you can jump straight back to.
        dash_root.addWidget(self._build_continue_strip())
        self._quick_controls = QWidget()
        self._quick_controls.setObjectName("surfaceContent")
        self._quick_row = QHBoxLayout(self._quick_controls)
        self._quick_row.setContentsMargins(4, 0, 4, 4)
        self._quick_row.addStretch(1)
        dash_root.insertWidget(0, self._quick_controls)
        self._apply_ui_layout()

    def _build_navigation_shell(self, outer):
        body = QHBoxLayout()
        self._body_layout = body
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self._sidebar = IllustratedSidebar()
        self._sidebar.setObjectName("sidebar")
        nav = QVBoxLayout(self._sidebar)
        self._sidebar_layout = nav
        nav.setContentsMargins(10, 14, 10, 14)
        nav.setSpacing(12)
        self._nav_buttons = []
        self._page_names = ["Overview", "Browse", "History", "Library", "Statistics", "Settings", "Activity log"]
        self._page_descriptions = [
            "Your anime, in the moment.", "Discover your next favourite.", "Pick up where you left off.",
            "Your AniList collection, all in one place.", "A little perspective on your watch time.",
            "Make AniPresence feel like you.", "Connection and detection updates."]
        for index, name in enumerate(self._page_names):
            if index == self._settings_tab_index:
                nav.addStretch(1)
            button = QPushButton(name)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.setIconSize(QSize(18, 18))
            button.setToolTip(name)
            button.setAccessibleName(name)
            button.clicked.connect(lambda checked=False, i=index: self._navigate(i))
            nav.addWidget(button)
            self._nav_buttons.append(button)
        self._sidebar_account = QPushButton("Default profile\nAniList · Add an account")
        self._sidebar_account.setObjectName("profileButton")
        self._sidebar_account.setIconSize(QSize(34, 34))
        self._sidebar_account.clicked.connect(self._show_account_menu)
        nav.addSpacing(14)
        nav.addWidget(self._sidebar_account)
        body.addWidget(self._sidebar)
        content = QVBoxLayout()
        self._content_layout = content
        content.setSpacing(0)
        self._page_heading = IllustratedHeader()
        heading = QVBoxLayout(self._page_heading)
        heading.setContentsMargins(20, 20, 20, 16)
        heading.setSpacing(4)
        self._page_title = QLabel()
        self._page_title.setObjectName("pageTitle")
        self._page_description = QLabel()
        self._page_description.setObjectName("pageDescription")
        self._page_description.setWordWrap(True)
        heading.addWidget(self._page_title)
        heading.addWidget(self._page_description)
        content.addWidget(self._page_heading)
        self._cinema_nav = QWidget()
        self._cinema_nav.setObjectName("surfaceContent")
        bar = QHBoxLayout(self._cinema_nav); bar.setContentsMargins(8, 0, 8, 8); bar.setSpacing(4)
        self._cinema_nav_buttons = []
        for i, name in enumerate(("Home", "Browse", "History", "Watchlist", "Watch time", "Settings", "Activity")):
            button = QPushButton(name); button.setObjectName("cinemaNav"); button.setCheckable(True)
            button.setToolTip(self._page_names[i])
            button.clicked.connect(lambda checked=False,index=i:self._navigate(index))
            bar.addWidget(button);self._cinema_nav_buttons.append(button)
        bar.addStretch()
        self._cinema_account = QPushButton("Account");self._cinema_account.setObjectName("cinemaPill")
        self._cinema_account.clicked.connect(self._show_account_menu);bar.addWidget(self._cinema_account)
        content.addWidget(self._cinema_nav)
        self._cinema_nav.hide()
        content.addWidget(self.stack, 1)
        body.addLayout(content, 1)
        outer.addLayout(body, 1)
        self._sync_navigation(0)

    def _sync_navigation(self, index):
        from .modern import navigation_icon
        for i, button in enumerate(self._nav_buttons):
            button.setChecked(i == index)
            color = "#ffffff" if i == index else ("#657086" if self._applied_theme == "light" else "#a4aec3")
            button.setIcon(navigation_icon(i, color))
        self._page_title.setText(self._page_names[index])
        self._page_description.setText(self._page_descriptions[index])
        if self.cfg.data.ui_layout=="cinema" and hasattr(self,"_details") and self.stack.currentWidget() is self._details:
            self._page_title.setText("Anime details")
            self._page_description.setText(self._details.entry.get("title") or "")
        self._page_heading.variant = index
        self._page_heading.update()
        if hasattr(self, "_cinema_nav_buttons"):
            for i, button in enumerate(self._cinema_nav_buttons):
                button.setChecked(i == index)
                button.setIcon(navigation_icon(i, "#aaaaaa"))
                button.setText(("", "", "", "", "", "", "")[i] if self.width()<900 else
                               ("Home","Browse","History","Watchlist","Watch time","Settings","Activity")[i])

    def _navigate(self, index):
        if self.tabbar.currentIndex() == index:
            self._on_tab_changed(index)
        else:
            self.tabbar.setCurrentIndex(index)

    def _toggle_sidebar(self):
        if self.cfg.data.ui_layout=="classic":return
        self.cfg.set(sidebar_hidden=not self.cfg.data.sidebar_hidden)
        self._sync_sidebar()

    def _sync_sidebar(self):
        modern=self.cfg.data.ui_layout!="classic"
        self._sidebar.setVisible(modern and not self.cfg.data.sidebar_hidden)
        self.sidebar_toggle.setVisible(modern)
        self.sidebar_toggle.setChecked(modern and not self.cfg.data.sidebar_hidden)
        self.sidebar_toggle.setToolTip(("Show" if self.cfg.data.sidebar_hidden else "Hide")+" sidebar · Ctrl+B")
        self.sidebar_toggle.setAccessibleName(self.sidebar_toggle.toolTip())

    def _open_quick_search(self):
        from .quick_search import QuickSearch
        if not hasattr(self,"_quick_search"):self._quick_search=QuickSearch(self)
        self._quick_search.present()

    def _show_account_menu(self):
        menu = QMenu(self)
        self._account_menu = menu
        active = self.controller.active_anilist_account
        for account in ["", *self.controller.anilist_accounts]:
            connected = bool(self.cfg.data.anilist_sessions.get(account.casefold())) or bool(
                account and self.cfg.data.anilist_access_token and self.cfg.data.anilist_sync_account.casefold() == account.casefold())
            action = menu.addAction((account or "Default profile") + (" · Connected" if connected else " · Public" if account else ""))
            action.setCheckable(True); action.setChecked(account == active)
            action.triggered.connect(lambda checked=False, name=account: self._switch_account_menu(name))
        menu.addSeparator()
        menu.addAction("Add / connect account…", self._connect_account_menu)
        menu.addAction("Account settings", self._show_account_settings)
        anchor = self._sidebar_account
        menu.popup(anchor.mapToGlobal(QPoint(0, anchor.height())))

    def _switch_account_menu(self, name):
        index = self.anilist_combo.findData(name)
        if index >= 0:
            self.anilist_combo.setCurrentIndex(index)

    def _connect_account_menu(self):
        self._show_account_settings()
        self._on_anilist_login()

    def _sync_more_actions(self):
        for action, button in self._more_links:
            action.setEnabled(button.isEnabled())

    def _on_layout_changed(self, index):
        self._tab_anim_stop()
        self.cfg.set(ui_layout=self.layout_combo.itemData(index))
        self._apply_ui_layout()
        self._apply_stylesheet()

    def _apply_ui_layout(self):
        """Rearrange the existing widgets, preserving live state and callbacks."""
        mode = self.cfg.data.ui_layout
        modern = mode != "classic"
        cinema = mode == "cinema"
        if self._layout_mode == mode:
            return
        was_details = self.stack.currentWidget() in (self._details_v2,self._details_v3)
        old_details = self._details
        self._layout_mode = mode
        self._history_state = None
        self._stats_state = None
        self.centralWidget().setObjectName("cinemaRoot" if cinema else "modernRoot" if modern else "classicRoot")
        self._details = self._details_v3 if cinema else self._details_v2
        self._sync_sidebar()
        self._cinema_nav.hide()
        self._page_heading.setVisible(modern and (cinema or not was_details))
        self._tabs_host.setVisible(not modern)
        self._classic_actions.setVisible(not modern)
        self._modern_actions.setVisible(modern)
        self._quick_controls.setVisible(modern)
        self._appearance_colors.setVisible(modern)
        self._history_modern.setVisible(modern)
        self._history_classic.setVisible(not modern)
        self._activity_panel.setVisible(modern)
        self._log_classic.setVisible(not modern)
        self._analytics.setVisible(modern)
        self._stats_classic.setVisible(not modern)
        self._show_library_calendar(False)
        if getattr(self, "_last_library_payload", None):
            self._on_library_loaded(self._last_library_payload)
        for btn in (self.win_min_btn,self.win_max_btn,self.win_close_btn):
            btn.setProperty("modern",modern)
            btn.setFixedSize(30,26) if modern else btn.setFixedSize(14,14)
            btn.setText(btn._current_glyph() if modern else "")
        self._now_card_layout.removeWidget(self._now_info)
        self._now_top_row.removeWidget(self._now_info)
        if modern:
            self._sidebar_layout.insertWidget(0,self._brand)
            self._outer_layout.insertWidget(0,self.title_bar)
            self._brand.setMaximumWidth(16777215)
            self._dashboard_layout.setAlignment(Qt.AlignTop)
            self._dashboard_layout.setStretch(1, 0)
            self._preview_frame.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
            self._now_info.layout().addWidget(self._modern_actions)
            self.open_site_btn.setObjectName("primaryAction")
            self._now_column.setAlignment(Qt.AlignTop)
            self._left_pane.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
            self._now_info_layout.setAlignment(Qt.AlignVCenter)
            self._general_settings.layout().addWidget(self._controls_card)
            self._account_settings.layout().insertWidget(1, self._account_card)
            self._connection_settings.layout().addWidget(self._actions_card)
            self._now_top_row.addWidget(self._now_info, 1)
            self._modern_action_row.insertWidget(0, self.open_site_btn)
            self._modern_action_row.insertWidget(1, self.next_ep_btn)
            self._quick_row.insertWidget(0, self.rpc_check)
            self._quick_row.insertWidget(1, self.detection_check)
            self._appearance_colors.layout().addWidget(self.theme_combo)
            self._appearance_colors.layout().addWidget(self.accent_combo)
            self._left_pane.setMinimumWidth(0)
        else:
            self._header_layout.insertWidget(0,self._brand,1)
            self._outer_layout.insertWidget(0,self.title_bar)
            self._brand.setMaximumWidth(180)
            self._brand.setFixedHeight(44)
            self._dashboard_layout.setAlignment(Qt.AlignmentFlag(0))
            self._dashboard_layout.setStretch(1, 1)
            self._preview_frame.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
            self._now_card_layout.addWidget(self._modern_actions)
            self.open_site_btn.setObjectName("ghost")
            self._now_column.setAlignment(Qt.AlignmentFlag(0))
            self._left_pane.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
            self._now_info_layout.setAlignment(Qt.AlignmentFlag(0))
            self._dashboard_row.addWidget(self._controls_card, 4)
            for index,card in zip((4,6),(self._account_card,self._actions_card)):
                self._classic_controls_layout.insertWidget(index,card)
            self._now_card_layout.insertWidget(1, self._now_info)
            self._classic_action_layout.insertWidget(0, self.open_site_btn)
            self._classic_action_layout.insertWidget(4, self.next_ep_btn)
            self._controls_grid.addWidget(self.rpc_check, 0, 0)
            self._controls_grid.addWidget(self.detection_check, 0, 1)
            index = self._header_layout.indexOf(self.win_min_btn)
            self._header_layout.insertWidget(index, self.theme_combo)
            self._header_layout.insertWidget(index + 1, self.accent_combo)
            self._left_pane.setMinimumWidth(420)
        # Reparented controls may have been hidden with their previous page.
        for widget in (self._controls_card, self._now_info, self.open_site_btn,
                       self.next_ep_btn, self.rpc_check, self.detection_check,
                       self.theme_combo, self.accent_combo):
            widget.show()
        self._brand.show()
        if cinema:
            self._header_layout.insertWidget(0,self._brand)
            self._brand.setMaximumWidth(170);self._brand.setFixedHeight(42)
        self.title_bar.show()
        for card in (self._account_card,self._actions_card):card.show()
        for sep in self._controls_card.findChildren(QFrame):
            if sep.objectName()=="sep":sep.setVisible(not modern)
        self.progress.setTextVisible(not modern)
        self._update_header_for_width()
        self._update_cover_size()
        QTimer.singleShot(0, self._update_cover_size)
        if hasattr(self, "_last_recent"):
            self._on_recent(self._last_recent)
        if was_details:
            if old_details is not self._details:
                self._details.open(old_details.entry,remember=False)
                self._details.history = list(old_details.history)
            self.stack.setCurrentWidget(self._details)
        else:
            self._on_tab_changed(self.tabbar.currentIndex())
        self._browse.configure_layout(cinema)
        if not cinema:self._refresh_stats(visible_only=True)
        self._scrolling.install() if hasattr(self,"_scrolling") else None

    def _show_all_stats(self):
        self._stats_classic.setVisible(not self._stats_classic.isVisible())
        if self._stats_classic.isVisible():
            self._refresh_stats()
            self._stats_page.ensureWidgetVisible(self.stats_search)

    def _show_account_settings(self):
        self.tabbar.setCurrentIndex(self._settings_tab_index)
        self._account_section.expand()
        self._settings_page.ensureWidgetVisible(self.anilist_combo)

    def _update_layout_spacing(self):
        if not hasattr(self, "_body_layout"):
            return
        modern = self.cfg.data.ui_layout == "modern"
        compact = self.cfg.data.compact
        inset = (8 if self.width() < 760 else 12 if compact else 16) if modern else 0
        self._body_layout.setContentsMargins(inset, inset if modern else 0, inset, inset)
        gutter = (12 if self.width() < 760 or compact else 20) if modern else (14 if compact else 16)
        self._body_layout.setSpacing(gutter if modern else 0)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._page_heading.layout().setContentsMargins(gutter, 16, gutter, 16)
        self._header_layout.setContentsMargins(inset if modern else 16, 8, inset if modern else 16, 0 if modern else 2)
        if self.cfg.data.ui_layout == "cinema":
            self._body_layout.setContentsMargins(16,0,16,12)
            self._body_layout.setSpacing(12)
            self._page_heading.setFixedHeight(112)
        if modern:
            self._page_heading.setFixedHeight(112)
            self._brand.setFixedHeight(112 - self._sidebar_layout.contentsMargins().top())
        if modern and hasattr(self,"_modern_actions"):
            target = self._now_card_layout if self.width()<1000 else self._now_info_layout
            if self._modern_actions.parentWidget() != target.parentWidget():
                target.addWidget(self._modern_actions)
                self._modern_actions.show()
        for i in range(self.stack.count()):
            page = self.stack.widget(i)
            host = page.widget() if isinstance(page, QScrollArea) else page
            host.layout().setContentsMargins(0 if modern else gutter, gutter if modern else 4,
                                            0 if modern else gutter, 0 if modern else gutter)
        for host in (self._history_classic,self._stats_classic,self._log_classic):
            host.layout().setContentsMargins(0,0,0,0)
        if hasattr(self, "_stats_summary_grid"):
            columns = 2 if self.width() < 900 else 4
            if self._stats_grid_columns != columns:
                self._stats_grid_columns = columns
                for card in self._stat_frames:
                    self._stats_summary_grid.removeWidget(card)
                for i, card in enumerate(self._stat_frames):
                    self._stats_summary_grid.addWidget(card, i // columns, i % columns)
                for i in range(4):
                    self._stats_summary_grid.setColumnStretch(i, 1 if i < columns else 0)
        if (getattr(self, "_chart_view", None) is not None and hasattr(self, "_chart_days")
                and getattr(self, "_chart_wide", None) != (self.width() >= 1200)):
            self._update_stats_chart(self._chart_days)

    def _reflow_controls(self):
        direction = QHBoxLayout.TopToBottom if self.cfg.data.ui_layout=="cinema" and self.width()<700 else QHBoxLayout.LeftToRight
        for row in getattr(self,"_settings_responsive_rows",()):
            row.setDirection(direction)
        if not hasattr(self, "_control_boxes"):
            return
        modern = self.cfg.data.ui_layout != "classic"
        columns = 1 if modern and self.width() < 760 else 2
        key = (modern, columns)
        if getattr(self, "_control_grid_mode", None) == key:
            return
        self._control_grid_mode = key
        for checkbox in self._control_boxes:
            self._controls_grid.removeWidget(checkbox)
        boxes = self._control_boxes[2:] if modern else self._control_boxes
        for i, checkbox in enumerate(boxes):
            self._controls_grid.addWidget(checkbox, i // columns, i % columns)

    # ----------------------------------------------------- continue watching
    def _on_tmdb_changed(self):
        credential = self.tmdb_edit.text().strip()
        if credential == self.cfg.data.tmdb_credential:
            return
        self.cfg.set(tmdb_credential=credential)
        self._artwork.configure(credential, self.cfg.data.cover_source)
        self._refresh_artwork_views()

    def _on_cover_source_changed(self, index):
        source = self.cover_source_combo.itemData(index)
        self.cfg.set(cover_source=source)
        self._artwork.configure(self.cfg.data.tmdb_credential, source)
        self._refresh_artwork_views()

    def _on_artwork_ready(self):
        if not self._artwork_timer.isActive():
            self._artwork_timer.start()

    def _refresh_artwork_views(self):
        if self._quitting:
            return
        self._images.invalidate_missing()
        self._repaint_artwork()
        if getattr(self, "_raw_detection_payload", None) and self._current_payload:
            self._on_detection(self._raw_detection_payload)

    def _repaint_artwork(self):
        if self._quitting:
            return
        if self.cfg.data.ui_layout == "cinema":
            from .details import Artwork
            if self._cinema_overview.isVisible(): self._cinema_overview.refresh(refresh_cards=False)
            for card in self._cinema_history.cards:
                if card.isVisible(): card.update()
            for image in self._cinema_stats.findChildren(Artwork):
                if image.isVisible():image.update()
        if hasattr(self, "_details") and self._details.isVisible():
            self._details.refresh_artwork()
        self._refresh_overview_logo()
        for view in self.findChildren(AnimeList):
            if view.isVisible():
                view.viewport().update()
        if hasattr(self, "_library_modern") and self._library_modern.isVisible():
            self._library_modern.grid.viewport().update()
        if hasattr(self, "_browse") and self._browse.isVisible():
            self._browse.grid.viewport().update()
        if hasattr(self, "_continue_frame") and self._continue_frame.isVisible():
            for card in self._continue_frame.cards:
                card.update()

    def _queue_artwork_repaint(self):
        if not self._quitting and not self._image_repaint_timer.isActive():
            self._image_repaint_timer.start()

    def _build_continue_strip(self):
        from .continue_watching import ContinueWatching
        frame = ContinueWatching(self._cover_cache_path)
        frame.configure(self.cfg.data.accent)
        frame.activated.connect(self._open_anime)
        frame.removed.connect(self._remove_continue)
        frame.view_all.connect(lambda: self.tabbar.setCurrentIndex(self._history_tab_index))
        self._continue_frame = frame
        self._refresh_continue()
        return frame

    def _refresh_continue(self):
        """Combine resumable title records with actual per-episode playback."""
        frame = getattr(self, "_continue_frame", None)
        if frame is None:
            return
        from ..core.media_identity import MediaIdentity
        titles = self.controller.watchlog.continue_list(None)
        history = self.controller.watchlog.history(self.controller.recent.items(), copy_result=False)
        resolver = MediaIdentity([*titles, *history])
        local = {}
        for entry in sorted(history, key=lambda e: e.get("last_watched") or 0, reverse=True):
            if entry.get("history_source") == "Local" and entry.get("url"):
                local.setdefault(resolver.key(entry), entry)
        entries = []
        for entry in titles:
            measured = local.get(resolver.key(entry))
            if measured:
                entry = dict(entry, **{k: v for k, v in measured.items() if v is not None})
            entries.append(entry)
        entries.sort(key=lambda e: e.get("last_watched") or 0, reverse=True)
        cinema=self.cfg.data.ui_layout=="cinema"
        entries = entries[:4] if cinema else [self._artwork.enrich(e, episode=True, priority=2) for e in entries[:4]]
        frame.set_entries(entries)
        if hasattr(self,"_cinema_overview") and self.cfg.data.ui_layout == "cinema":
            self._cinema_overview.refresh()
        if cinema:return  # V3 requests artwork from visible cards, not hidden legacy widgets.
        # Reuse the normal history cover downloader for AniList fallback images.
        for entry in entries:
            for field in ("episode_art", "backdrop", "banner_url", "cover"):
                cover = entry.get(field) or ""
                if cover and not self._cover_cache_path(cover).exists() and cover not in self._continue_icon_fetching:
                    self._continue_icon_pending[cover] = frame
                    self._continue_icon_fetching.add(cover)
                    self._nam.get(QNetworkRequest(QUrl(cover)))

    def _remove_continue(self, title):
        """Remove one title from the continue-watching strip."""
        self.controller.remove_continue_entry(title)
        self._continue_fingerprint = None  # force a rebuild of the strip
        self._refresh_continue()
        if getattr(self, "_stat_cards", None):
            self._refresh_stats()

    # --------------------------------------------------------------- stats
    def _build_library_page(self, margin, compact):
        """Assemble the "Library" tab: the active AniList account's list grouped
        by status, refreshed live from AniList (public list, no OAuth needed)."""
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
        self._library_page = page

        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(self._field_label("Library"))
        header.addStretch(1)
        self.library_account_label = QLabel("")
        self.library_account_label.setObjectName("subLabel")
        header.addWidget(self.library_account_label)
        self.library_refresh_btn = QPushButton("Refresh")
        self.library_refresh_btn.setObjectName("ghost")
        self.library_refresh_btn.setToolTip(
            "Re-fetch the active AniList account's list. "
            "Requires a public list (username in Controls)."
        )
        self.library_refresh_btn.clicked.connect(self._on_refresh_library)
        header.addWidget(self.library_refresh_btn)
        layout.addLayout(header)

        self.library_search = QLineEdit()
        self.library_search.setPlaceholderText("Filter by title, genre, or score…")
        self.library_search.setClearButtonEnabled(True)
        self.library_search.textChanged.connect(self._on_library_search)
        layout.addWidget(self.library_search)

        self._library_tabs = QTabBar()
        self._library_tabs.setDrawBase(False)
        self._library_tabs.addTab("Calendar")
        for label in ("Watching", "Planning", "Completed", "Paused", "Dropped"):
            self._library_tabs.addTab(label)
        self._library_tabs.currentChanged.connect(self._on_library_section_changed)
        layout.addWidget(self._library_tabs)
        self._library_stack = QStackedWidget()

        calendar_page = QWidget()
        cal_layout = QVBoxLayout(calendar_page)
        cal_layout.setContentsMargins(0, 8, 0, 0)
        cal_layout.setSpacing(8)
        self._calendar_season_label = QLabel("")
        self._calendar_season_label.setObjectName("subLabel")
        cal_layout.addWidget(self._calendar_season_label)
        self._calendar_list = AnimeList()
        self._calendar_list.setIconSize(QSize(38, 53))
        self._calendar_list.itemActivated.connect(self._open_anilist_entry)
        self._calendar_list.setContextMenuPolicy(Qt.CustomContextMenu)
        cal_layout.addWidget(self._calendar_list, 1)
        self._library_stack.addWidget(calendar_page)

        self._library_section_lists = {}
        for key in ("Watching", "Planning", "Completed", "Paused", "Dropped"):
            section_page = QWidget()
            sl = QVBoxLayout(section_page)
            sl.setContentsMargins(0, 8, 0, 0)
            sl.setSpacing(8)
            listw = AnimeList()
            listw.setIconSize(QSize(38, 53))
            listw.itemActivated.connect(self._open_anilist_entry)
            listw.setContextMenuPolicy(Qt.CustomContextMenu)
            self._library_section_lists[key] = listw
            sl.addWidget(listw, 1)
            self._library_stack.addWidget(section_page)
        layout.addWidget(self._library_stack, 1)
        from .library import LibraryPanel
        self._library_modern = LibraryPanel()
        self._library_modern.activated.connect(self._open_anime)
        self._library_modern.refresh_requested.connect(self._on_refresh_library)
        self._library_modern.calendar_requested.connect(lambda: self._show_library_calendar(True))
        self._library_modern.status_changed.connect(self.controller.change_library_status)
        layout.addWidget(self._library_modern, 1)
        self._library_back = QPushButton("Back to library")
        self._library_back.clicked.connect(lambda: self._show_library_calendar(False))
        layout.addWidget(self._library_back)

    def _show_library_calendar(self, calendar):
        modern = self.cfg.data.ui_layout != "classic"
        self._library_modern.setVisible(modern and not calendar)
        self._library_modern.configure(self._applied_theme == "light")
        self._library_stack.setVisible(not modern or calendar)
        self._library_tabs.setVisible(not modern)
        self.library_search.setVisible(not modern or calendar)
        self._library_back.setVisible(modern and calendar)
        self._library_page.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff if modern else Qt.ScrollBarAsNeeded)
        if modern and calendar:
            self._library_stack.setCurrentIndex(0)
            if getattr(self, "_last_calendar_payload", None):
                self._on_calendar_loaded(self._last_calendar_payload)

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
        cards = QGridLayout()
        self._stats_summary_grid = cards
        self._stat_frames = []
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
            card.setMinimumHeight(78 if compact else 88)
            self._stat_frames.append(card)
            c = QVBoxLayout(card)
            c.setContentsMargins(self._pad, self._pad, self._pad, self._pad)
            c.setSpacing(4)
            value = QLabel("—")
            value.setObjectName("statValue")
            value.setWordWrap(True)
            value.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            cap = QLabel(caption)
            cap.setObjectName("sectionLabel")
            cap.setWordWrap(True)
            c.addWidget(value)
            c.addWidget(cap)
            cards.addWidget(card, 0, len(self._stat_frames) - 1)
            self._stat_cards[key] = value
        layout.addLayout(cards)

        # AniList backfill: seed the per-title totals from a public list.
        self.import_btn = QPushButton("Import from AniList")
        self.import_btn.setObjectName("ghost")
        self.import_btn.setToolTip(
            "Refresh AniList list totals and available activity. Daily charts combine "
            "measured playback with clearly identified feed-based estimates."
        )
        self.import_btn.clicked.connect(self._on_import_anilist)

        # Daily hours chart (QtCharts) — degrades to a hint label if missing.
        chart_card = QFrame()
        chart_card.setObjectName("card")
        cc = QVBoxLayout(chart_card)
        cc.setContentsMargins(self._pad, self._pad, self._pad, self._pad)
        cc.setSpacing(4)
        chart_header = QHBoxLayout()
        self._chart_caption = self._field_label("WATCH ACTIVITY · LAST 14 DAYS")
        chart_header.addWidget(self._chart_caption)
        chart_header.addStretch(1)
        chart_header.addWidget(self.import_btn)
        cc.addLayout(chart_header)
        source_hint = QLabel("Measured playback + AniList estimates · Feed dates are list-update dates.")
        source_hint.setObjectName("subLabel")
        source_hint.setWordWrap(True)
        cc.addWidget(source_hint)
        self._chart_view = None
        self._chart_hint = None
        if QTCHARTS_OK:
            view = QChartView()
            view.setRenderHint(QPainter.Antialiasing)
            # Keep axes and labels fully visible in a normally-sized window.
            view.setMinimumHeight(300)
            view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
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
        self.stats_search = QLineEdit()
        self.stats_search.setPlaceholderText("Search your watch history and imported lists…")
        self.stats_search.setClearButtonEnabled(True)
        self.stats_search.textChanged.connect(self._apply_stats_filter)
        layout.addWidget(self.stats_search)
        self._stats_stack = QStackedWidget()
        self._stats_stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._stats_stack.setMinimumHeight(580)

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
        self._stats_list = AnimeList()
        self._stats_list.setIconSize(QSize(38, 53))
        self._stats_list.itemActivated.connect(self._resume_stats_item)
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
            listw = AnimeList()
            listw.setIconSize(QSize(38, 53))
            listw.itemActivated.connect(self._open_anilist_entry)
            self._section_lists[key] = listw
            pl.addWidget(listw, 1)
            self._stats_stack.addWidget(page)

        layout.addWidget(self._stats_stack, 1)

    # ------------------------------------------------------------- settings
    def _build_settings_page(self, margin, compact):
        """Assemble the "Settings" tab: global-hotkey recorder rows plus the
        less-common knobs that don't belong on the main dashboard."""
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
        self._settings_page = page

        appearance = QFrame()
        appearance.setObjectName("card")
        appearance_layout = QVBoxLayout(appearance)
        appearance_layout.setContentsMargins(self._pad, self._pad, self._pad, self._pad)
        appearance_layout.setSpacing(10)
        appearance_layout.addWidget(self._field_label("APPEARANCE"))
        self.layout_combo = QComboBox()
        for caption, value in (("V1 · Original", "classic"),("V2 · Current", "modern"),("V3 · Cinema", "cinema")):
            self.layout_combo.addItem(caption,value)
        self.layout_combo.setCurrentIndex(self.layout_combo.findData(self.cfg.data.ui_layout))
        self.layout_combo.currentIndexChanged.connect(self._on_layout_changed)
        appearance_layout.addWidget(self.layout_combo)
        self.glass_check = QCheckBox("Glass panels && soft accent lighting")
        self.glass_check.setChecked(self.cfg.data.glass_effects)
        self.glass_check.toggled.connect(self._on_glass_changed)
        appearance_layout.addWidget(self.glass_check)
        self.animations_check = QCheckBox("Smooth page transitions && playback progress")
        self.animations_check.setChecked(self.cfg.data.smooth_animations)
        self.animations_check.toggled.connect(self._on_animations_changed)
        appearance_layout.addWidget(self.animations_check)
        hint = QLabel("V1: original tabs · V2: illustrated sidebar · V3: cinematic sidebar and artwork. Switch live.")
        hint.setWordWrap(True)
        hint.setObjectName("subLabel")
        appearance_layout.addWidget(hint)
        self._appearance_colors = QWidget()
        self._appearance_colors.setObjectName("surfaceContent")
        colors = QHBoxLayout(self._appearance_colors)
        colors.setContentsMargins(0, 0, 0, 0)
        colors.addWidget(QLabel("Theme & accent"))
        colors.addStretch(1)
        appearance_layout.addWidget(self._appearance_colors)
        layout.addWidget(appearance)

        from PySide6.QtWidgets import QFontComboBox
        from ..fonts import load_custom_font
        load_custom_font(self.cfg.data.font_path)
        font_row = QHBoxLayout()
        font_row.addWidget(QLabel("Interface font"))
        self.font_combo = QFontComboBox()
        self.font_combo.setCurrentFont(QFont(self.cfg.data.font_family))
        self.font_combo.currentFontChanged.connect(lambda font: self._set_font_family(font.family()))
        font_row.addWidget(self.font_combo, 1)
        font_file = QPushButton("Import font…"); font_file.clicked.connect(self._import_font)
        font_reset = QPushButton("Reset"); font_reset.clicked.connect(lambda: self._set_font_family("Figtree", reset=True))
        font_row.addWidget(font_file); font_row.addWidget(font_reset)
        appearance_layout.addLayout(font_row)
        self.overview_logo_check = QCheckBox("Use the TMDB anime logo instead of the Overview title")
        self.overview_logo_check.setChecked(self.cfg.data.overview_title_logo)
        self.overview_logo_check.toggled.connect(self._set_overview_logo)
        appearance_layout.addWidget(self.overview_logo_check)
        self.font_message = QLabel("Installed fonts or your own .ttf / .otf file"); self.font_message.setObjectName("subLabel")
        self.font_message.setWordWrap(True); appearance_layout.addWidget(self.font_message)

        streaming = QFrame(); streaming.setObjectName("card")
        stream_layout = QVBoxLayout(streaming); stream_layout.setContentsMargins(self._pad, self._pad, self._pad, self._pad)
        stream_layout.addWidget(self._field_label("OPEN ANIME ON"))
        from ..core.streaming import SITES
        self.streaming_combo = QComboBox(); self.streaming_combo.addItems([*SITES, "Custom"])
        self.streaming_combo.setCurrentText(self.cfg.data.streaming_site)
        stream_layout.addWidget(self.streaming_combo)
        self.streaming_url = QLineEdit(self.cfg.data.streaming_search_url)
        self.streaming_url.setPlaceholderText("https://example.com/search?q={query}")
        self.streaming_url.setVisible(self.cfg.data.streaming_site == "Custom")
        stream_layout.addWidget(self.streaming_url)
        self.streaming_hint = QLabel("Watch actions open your selected site. Saved episodes on that site resume directly.")
        self.streaming_hint.setWordWrap(True); self.streaming_hint.setObjectName("subLabel"); stream_layout.addWidget(self.streaming_hint)
        self.streaming_combo.currentTextChanged.connect(self._save_streaming_site)
        self.streaming_url.editingFinished.connect(self._save_streaming_site)
        layout.addWidget(streaming)
        self._general_settings = QWidget(); self._general_settings.setObjectName("surfaceContent")
        QVBoxLayout(self._general_settings).setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._general_settings)

        # -- Global hotkeys -------------------------------------------------
        hotkeys = QFrame()
        hotkeys.setObjectName("card")
        hk = QVBoxLayout(hotkeys)
        hk.setContentsMargins(self._pad, self._pad, self._pad, self._pad)
        hk.setSpacing(10)
        hk.addWidget(self._field_label("GLOBAL HOTKEYS"))
        hint = QLabel(
            "Click a shortcut, then press the new combination. "
            "Global hotkeys work even when the window is hidden."
        )
        hint.setObjectName("subLabel")
        hint.setWordWrap(True)
        hk.addWidget(hint)

        self._hotkey_buttons = {}
        for hotkey_id in (self._HOTKEY_ID, self._HOTKEY_PAUSE_ID,
                          self._HOTKEY_CLEAR_ID, self._HOTKEY_NEXT_ID):
            row = QHBoxLayout()
            row.setSpacing(8)
            label = QLabel(self._HOTKEY_LABELS[hotkey_id])
            label.setObjectName("sectionLabel")
            row.addWidget(label, 1)
            btn = _HotkeyButton(
                getattr(self.cfg.data, self._HOTKEY_CONFIG[hotkey_id], ""),
                on_change=lambda spec, hid=hotkey_id: self._on_hotkey_changed(hid, spec),
                on_record_start=self._on_hotkey_record_start,
                on_record_end=self._on_hotkey_record_end,
            )
            btn.setFixedWidth(170)
            row.addWidget(btn)
            self._hotkey_buttons[hotkey_id] = btn
            hk.addLayout(row)
        hk.addWidget(self._sep())
        reset_row = QHBoxLayout()
        reset_row.addStretch(1)
        reset_btn = QPushButton("Reset to defaults")
        reset_btn.setObjectName("ghost")
        reset_btn.setToolTip("Restore Ctrl+Alt+A / P / X / N")
        reset_btn.clicked.connect(self._on_reset_hotkeys)
        reset_row.addWidget(reset_btn)
        hk.addLayout(reset_row)
        layout.addWidget(hotkeys)

        # -- Discord & browser --------------------------------------------
        conn = QFrame()
        self._connection_settings = conn
        conn.setObjectName("card")
        cc = QVBoxLayout(conn)
        cc.setContentsMargins(self._pad, self._pad, self._pad, self._pad)
        cc.setSpacing(10)
        cc.addWidget(self._field_label("DISCORD & BROWSER"))

        cc.addWidget(self._field_label("DISCORD APPLICATION ID"))
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
        cc.addLayout(id_row)

        broadcast_row = QHBoxLayout()
        broadcast_row.setSpacing(8)
        broadcast_label = QLabel("Broadcast")
        broadcast_label.setObjectName("sectionLabel")
        broadcast_row.addWidget(broadcast_label)
        self.broadcast_combo = QComboBox()
        self.broadcast_combo.addItems(["Discord pipe", "Vencord bridge", "Both"])
        self.broadcast_combo.setToolTip(
            "Where to announce your activity. 'Discord pipe' uses the local RPC "
            "connection (visible to you; others only if your app is verified). "
            "'Vencord bridge' posts to the bundled Vencord userplugin so friends "
            "see it without verification; 'Both' sends to both."
        )
        broadcast_row.addWidget(self.broadcast_combo, 1)
        cc.addLayout(broadcast_row)

        cc.addWidget(self._sep())

        grid = QHBoxLayout()
        grid.setSpacing(14)
        col1 = QVBoxLayout()
        col1.setSpacing(6)
        col1.addWidget(self._field_label("BROWSER"))
        self.browser_combo = QComboBox()
        self.browser_combo.addItems(["Chrome", "Brave", "Edge", "Opera"])
        self.browser_combo.setToolTip("Which browser to launch/connect to.")
        col1.addWidget(self.browser_combo)

        col2 = QVBoxLayout()
        col2.setSpacing(6)
        col2.addWidget(self._field_label("POLL INTERVAL (S)"))
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(2, 60)
        self.interval_spin.setValue(int(self.cfg.data.poll_interval_seconds))
        self.interval_spin.setToolTip(
            "How often the controller re-checks the player for changes."
        )
        col2.addWidget(self.interval_spin)
        col2.addStretch(1)

        grid.addLayout(col1, 1)
        grid.addLayout(col2, 1)
        cc.addLayout(grid)
        layout.addWidget(conn)

        artwork = QFrame()
        artwork.setObjectName("card")
        art_layout = QVBoxLayout(artwork)
        art_layout.setContentsMargins(self._pad, self._pad, self._pad, self._pad)
        art_layout.addWidget(self._field_label("TMDB ARTWORK & EPISODE NAMES"))
        self.tmdb_edit = QLineEdit(self.cfg.data.tmdb_credential)
        self.tmdb_edit.setEchoMode(QLineEdit.Password)
        self.tmdb_edit.setPlaceholderText("TMDB API key or API Read Access Token")
        self.tmdb_edit.editingFinished.connect(self._on_tmdb_changed)
        art_layout.addWidget(self.tmdb_edit)
        self.cover_source_combo = QComboBox()
        self.cover_source_combo.addItem("TMDB covers · AniList fallback", "tmdb")
        self.cover_source_combo.addItem("AniList covers", "anilist")
        self.cover_source_combo.setCurrentIndex(max(0, self.cover_source_combo.findData(self.cfg.data.cover_source)))
        self.cover_source_combo.currentIndexChanged.connect(self._on_cover_source_changed)
        art_layout.addWidget(self._field_label("COVER SOURCE"))
        art_layout.addWidget(self.cover_source_combo)
        hint = QLabel('Episode names and stills use TMDB with either cover source. '
                      '<a href="https://www.themoviedb.org/settings/api">Get a TMDB key</a>.')
        hint.setWordWrap(True); hint.setOpenExternalLinks(True); hint.setObjectName("subLabel")
        art_layout.addWidget(hint)
        attribution = QLabel('This product uses the TMDB API but is not endorsed or certified by TMDB.')
        attribution.setWordWrap(True); attribution.setObjectName("subLabel")
        art_layout.addWidget(attribution)
        layout.addWidget(artwork)

        # -- Custom anime sites ---------------------------------------------
        custom = QFrame()
        custom.setObjectName("card")
        cm = QVBoxLayout(custom)
        cm.setContentsMargins(self._pad, self._pad, self._pad, self._pad)
        cm.setSpacing(10)
        cm.addWidget(self._field_label("CUSTOM ANIME SITES"))
        hint = QLabel(
            "Streaming sites AniPresence doesn't know yet. Detection reads the "
            "page title and URL (e.g. episode number), just like the built-in "
            "generic sites — so add the domain and a display name."
        )
        hint.setObjectName("subLabel")
        hint.setWordWrap(True)
        cm.addWidget(hint)

        self._custom_sites = [dict(s) for s in (self.cfg.data.custom_sites or [])]
        self.custom_sites_view = QListWidget()
        self.custom_sites_view.setMaximumHeight(110)
        self.custom_sites_view.itemClicked.connect(self._on_custom_site_pick)
        cm.addWidget(self.custom_sites_view)

        add_row = QHBoxLayout()
        add_row.setSpacing(8)
        self.custom_name_edit = QLineEdit()
        self.custom_name_edit.setPlaceholderText("Site name, e.g. MyAnimeHub")
        self.custom_host_edit = QLineEdit()
        self.custom_host_edit.setPlaceholderText("Domain, e.g. myanimehub.to")
        self.custom_host_edit.returnPressed.connect(self._on_add_custom_site)
        add_btn = QPushButton("Add site")
        add_btn.setObjectName("ghost")
        add_btn.setToolTip("Add this site to the detector list (saves immediately).")
        add_btn.clicked.connect(self._on_add_custom_site)
        add_row.addWidget(self.custom_name_edit, 1)
        add_row.addWidget(self.custom_host_edit, 1)
        add_row.addWidget(add_btn)
        cm.addLayout(add_row)

        del_row = QHBoxLayout()
        del_row.addStretch(1)
        self.custom_del_btn = QPushButton("Remove selected")
        self.custom_del_btn.setObjectName("ghost")
        self.custom_del_btn.setToolTip("Stop detecting the selected site.")
        self.custom_del_btn.setEnabled(False)
        self.custom_del_btn.clicked.connect(self._on_remove_custom_site)
        del_row.addWidget(self.custom_del_btn)
        cm.addLayout(del_row)
        self._refresh_custom_sites()
        layout.addWidget(custom)

        # -- Misc settings --------------------------------------------------
        misc = QFrame()
        misc.setObjectName("card")
        mm = QVBoxLayout(misc)
        mm.setContentsMargins(self._pad, self._pad, self._pad, self._pad)
        mm.setSpacing(10)
        mm.addWidget(self._field_label("ANILIST CONNECTION & SYNC"))
        self._account_settings = misc

        min_row = QHBoxLayout()
        min_label = QLabel("Minimum match score (%)")
        min_label.setObjectName("sectionLabel")
        min_row.addWidget(min_label, 1)
        self.match_spin = QSpinBox()
        self.match_spin.setRange(0, 100)
        self.match_spin.setValue(int(self.cfg.data.min_match_score))
        self.match_spin.setToolTip(
            "How confident a title match must be before it's shown. Lower = "
            "more permissive."
        )
        min_row.addWidget(self.match_spin)
        cc.addLayout(min_row)

        ttl_row = QHBoxLayout()
        ttl_label = QLabel("AniList cache (days)")
        ttl_label.setObjectName("sectionLabel")
        ttl_row.addWidget(ttl_label, 1)
        self.ttl_spin = QSpinBox()
        self.ttl_spin.setRange(0, 90)
        self.ttl_spin.setValue(int(self.cfg.data.anilist_cache_ttl_days))
        self.ttl_spin.setToolTip("How long fetched AniList data stays cached.")
        ttl_row.addWidget(self.ttl_spin)
        cc.addLayout(ttl_row)

        sync_row = QHBoxLayout()
        self.anilist_sync_check = QCheckBox("Keep AniList in sync (two-way)")
        self.anilist_sync_check.setChecked(self.cfg.data.anilist_sync_enabled)
        self.anilist_sync_check.setToolTip(
            "Pull your AniList list into AniPresence and push episodes that "
            "reach 90% playback. An access token lets the pull include private "
            "profiles and the account that owns the token."
        )
        sync_row.addWidget(self.anilist_sync_check, 1)
        self.anilist_sync_now_btn = QPushButton("Sync now")
        self.anilist_sync_now_btn.setToolTip(
            "Fetch the latest list from AniList now. Progress updates for "
            "watched episodes are pushed automatically."
        )
        sync_row.addWidget(self.anilist_sync_now_btn)
        mm.addLayout(sync_row)

        token_row = QHBoxLayout()
        token_label = QLabel("AniList access token")
        token_label.setObjectName("sectionLabel")
        token_row.addWidget(token_label, 1)
        self.anilist_token_edit = QLineEdit()
        self.anilist_token_edit.setEchoMode(QLineEdit.Password)
        self.anilist_token_edit.setPlaceholderText("Required for sync mutations")
        self.anilist_token_edit.setText(self.cfg.data.anilist_access_token)
        self.anilist_token_edit.setToolTip(
            "OAuth access token for an AniList account. It is stored in your local config."
        )
        token_row.addWidget(self.anilist_token_edit, 1)
        mm.addLayout(token_row)

        sync_hint = QLabel(
            "Two-way sync reads the token owner's AniList list (public or "
            "private) into the Library and Stats, and advances their episode "
            "progress at 90% playback. Without a token, the username selector "
            "still controls public-list imports; the sync destination is always "
            "the token's account."
        )
        sync_hint.setObjectName("subLabel")
        sync_hint.setWordWrap(True)
        mm.addWidget(sync_hint)

        self.anilist_sync_status = QLabel("")
        self.anilist_sync_status.setObjectName("subLabel")
        self.anilist_sync_status.setWordWrap(True)
        mm.addWidget(self.anilist_sync_status)

        login_row = QHBoxLayout()
        self.anilist_login_btn = QPushButton("Connect AniList")
        self.anilist_login_btn.clicked.connect(self._on_anilist_login)
        login_row.addWidget(self.anilist_login_btn)
        disconnect_btn = QPushButton("Disconnect")
        disconnect_btn.clicked.connect(self._on_anilist_disconnect)
        login_row.addWidget(disconnect_btn)
        self.anilist_retry_btn = QPushButton("Retry failed syncs")
        self.anilist_retry_btn.clicked.connect(self.controller.retry_anilist_sync)
        login_row.addWidget(self.anilist_retry_btn)
        mm.addLayout(login_row)
        self.anilist_client_edit = QLineEdit(self.cfg.data.anilist_client_id)
        self.anilist_client_edit.setPlaceholderText("AniList OAuth client ID (one-time setup)")
        self.anilist_client_edit.editingFinished.connect(
            lambda: self.cfg.set(anilist_client_id=self.anilist_client_edit.text().strip()))
        mm.addWidget(self.anilist_client_edit)
        self.anilist_secret_edit = QLineEdit()
        self.anilist_secret_edit.setEchoMode(QLineEdit.Password)
        self.anilist_secret_edit.setPlaceholderText("AniList Client Secret (authorization-code login)")
        self.anilist_secret_edit.setToolTip(
            "Enter your own application's secret when AniList rejects implicit login. "
            "Used only for this login attempt; it is not saved in config or included in the installer.")
        mm.addWidget(self.anilist_secret_edit)
        oauth_hint = QLabel(
            'Register AniPresence at <a href="https://anilist.co/settings/developer">AniList developer settings</a> '
            'with redirect URL <b>http://127.0.0.1:8794/callback</b>, then enter its client ID above. '
            'If AniList shows unsupported_grant_type, enter that application’s Client Secret above. '
            'This enables authorization-code login. The secret is used only for this attempt; '
            'Connect saves the resulting access token automatically.')
        oauth_hint.setOpenExternalLinks(True)
        oauth_hint.setWordWrap(True)
        oauth_hint.setObjectName("subLabel")
        mm.addWidget(oauth_hint)
        self.anilist_login_status = QLabel("")
        self.anilist_login_status.setWordWrap(True)
        mm.addWidget(self.anilist_login_status)
        self.sync_details = QLabel("")
        self.sync_details.setTextFormat(Qt.PlainText)
        self.sync_details.setWordWrap(True)
        mm.addWidget(self.sync_details)

        mm = cc
        mm.addWidget(self._field_label("Discord presence"))
        self.presence_options = {}
        for key, label in (
            ('presence_episode_names', 'Show episode names'),
            ('presence_airing_dates', 'Show next airing date'),
            ('presence_cover_art', 'Show cover art'),
            ('presence_paused_label', 'Show Paused label'),
        ):
            check = QCheckBox(label)
            check.setChecked(getattr(self.cfg.data, key))
            check.toggled.connect(lambda value, field=key: self._on_presence_option(field, value))
            self.presence_options[key] = check
            mm.addWidget(check)

        diagnostics = QFrame(); diagnostics.setObjectName("card")
        mm = QVBoxLayout(diagnostics); mm.setContentsMargins(self._pad, self._pad, self._pad, self._pad)
        layout.addWidget(diagnostics)
        mm.addWidget(self._field_label("Extension diagnostics"))
        self.extension_diagnostics = QLabel("")
        self.extension_diagnostics.setTextFormat(Qt.PlainText)
        self.extension_diagnostics.setWordWrap(True)
        mm.addWidget(self.extension_diagnostics)
        extension_folder_btn = QPushButton("Open installed extension folder")
        extension_folder_btn.clicked.connect(self._open_extension_folder)
        mm.addWidget(extension_folder_btn)

        updates = QFrame(); updates.setObjectName("card")
        mm = QVBoxLayout(updates); mm.setContentsMargins(self._pad, self._pad, self._pad, self._pad)
        layout.addWidget(updates)
        mm.addWidget(self._field_label("App updates"))
        self.auto_updates_check = QCheckBox("Check for updates on startup and every 6 hours")
        self.auto_updates_check.setChecked(self.cfg.data.auto_check_updates)
        self.auto_updates_check.toggled.connect(self._on_auto_updates_changed)
        mm.addWidget(self.auto_updates_check)
        self.update_feed_edit = QLineEdit(self.cfg.data.update_feed_url)
        self.update_feed_edit.setPlaceholderText("HTTPS release feed or GitHub latest-release API URL")
        self.update_feed_edit.editingFinished.connect(
            lambda: self.cfg.set(update_feed_url=self.update_feed_edit.text().strip()))
        mm.addWidget(self.update_feed_edit)
        update_row = QHBoxLayout()
        self.update_check_btn = QPushButton("Check now")
        self.update_check_btn.clicked.connect(lambda: self._check_for_updates())
        update_row.addWidget(self.update_check_btn)
        self.update_download_btn = QPushButton("Download installer")
        self.update_download_btn.clicked.connect(self._download_update)
        update_row.addWidget(self.update_download_btn)
        self.update_install_btn = QPushButton("Install update")
        self.update_install_btn.clicked.connect(self._install_update)
        update_row.addWidget(self.update_install_btn)
        mm.addLayout(update_row)
        self._settings_responsive_rows = (font_row,login_row,update_row)
        self.update_status_label = QLabel("")
        self.update_status_label.setTextFormat(Qt.PlainText)
        self.update_status_label.setWordWrap(True)
        mm.addWidget(self.update_status_label)

        mm = cc
        port_row = QHBoxLayout()
        port_label = QLabel("Chrome debugging port")
        port_label.setObjectName("sectionLabel")
        port_row.addWidget(port_label, 1)
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(int(self.cfg.data.chrome_debugging_port))
        self.port_spin.setToolTip(
            "Local port the browser exposes for page inspection. Must match "
            "the --remote-debugging-port your browser starts with."
        )
        port_row.addWidget(self.port_spin)
        mm.addLayout(port_row)

        paused_row = QHBoxLayout()
        self.paused_check = QCheckBox("Keep presence when video is paused")
        self.paused_check.setChecked(self.cfg.data.show_paused_presence)
        self.paused_check.setToolTip(
            "Leave the current presence up while playback is paused instead of "
            "resetting it."
        )
        paused_row.addWidget(self.paused_check, 1)
        mm.addLayout(paused_row)

        no_video_row = QHBoxLayout()
        self.hide_no_video_check = QCheckBox(
            "Hide presence when no video is detected"
        )
        self.hide_no_video_check.setChecked(self.cfg.data.hide_when_no_video)
        self.hide_no_video_check.setToolTip(
            "Clear the presence when nothing is playing instead of showing "
            "the idle/browsing state."
        )
        no_video_row.addWidget(self.hide_no_video_check, 1)
        mm.addLayout(no_video_row)

        self.settings_hint = QLabel("")
        self.settings_hint.setObjectName("subLabel")
        self.settings_hint.setWordWrap(True)
        mm.addWidget(self.settings_hint)
        layout.addWidget(misc)
        from .settings import SettingsSection
        sections = [(appearance, "Appearance & fonts", True), (streaming, "Streaming destination", True),
                    (self._general_settings, "General & privacy", False), (misc, "AniList account & sync", True),
                    (artwork, "Artwork & metadata", False), (conn, "Detection & Discord", False),
                    (custom, "Custom detection sites", False), (hotkeys, "Keyboard shortcuts", False),
                    (updates, "Updates", False), (diagnostics, "Extension diagnostics", False)]
        for content, _, _ in sections: layout.removeWidget(content)
        self._settings_sections = []
        for content, title, expanded in sections:
            section = SettingsSection(title, content, expanded)
            self._settings_sections.append(section); layout.addWidget(section)
            if content is misc: self._account_section = section
            if content is updates: self._updates_section = section
        layout.addStretch(1)

    def _save_streaming_site(self, *_):
        from ..core.streaming import valid_search_template
        site = self.streaming_combo.currentText()
        self.streaming_url.setVisible(site == "Custom")
        url = self.streaming_url.text().strip()
        if site == "Custom" and not valid_search_template(url):
            self.streaming_hint.setText("Enter an HTTP(S) search URL containing {query}, then press Enter or leave the field to save.")
            return
        self.cfg.set(streaming_site=site, streaming_search_url=url)
        self.streaming_hint.setText(f"Watch actions open on {site}. Saved episodes on that site resume directly.")

    def _set_overview_logo(self, enabled):
        self.cfg.set(overview_title_logo=enabled)
        self._refresh_overview_logo()

    def _refresh_overview_logo(self):
        if not hasattr(self, "anime_logo"):
            return
        payload = self._current_payload or {}
        self.anime_logo.url = payload.get("title_logo") or ""
        show = bool(self.cfg.data.overview_title_logo and self.anime_logo.url
                    and not self.anime_logo.pixmap().isNull())
        self.anime_logo.setVisible(show)
        self.anime_title.setVisible(not show)
        self.anime_logo.update()

    def _set_font_family(self, family, reset=False):
        self.cfg.set(font_family=family, **({"font_path": ""} if reset else {}))
        with QSignalBlocker(self.font_combo):
            self.font_combo.setCurrentFont(QFont(family))
        self._apply_stylesheet()

    def _import_font(self):
        from PySide6.QtWidgets import QFileDialog
        from ..fonts import load_custom_font
        import shutil
        path, _ = QFileDialog.getOpenFileName(self, "Import interface font", "", "Fonts (*.ttf *.otf)")
        if not path: return
        family = load_custom_font(path)
        if not family:
            self.font_message.setText("This file could not be loaded as a font.")
            return
        try:
            source = Path(path)
            target = APP_DIR / "fonts" / (hashlib.sha256(source.read_bytes()).hexdigest()[:20] + source.suffix.lower())
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.resolve() != target.resolve(): shutil.copyfile(source, target)
            self.cfg.set(font_path=str(target))
            self._set_font_family(family)
            self.font_message.setText(f"Imported {family} · saved with your app settings")
        except OSError as exc:
            self.font_message.setText(f"Could not save font: {exc}")

    # ---------------------------------------------------------- hotkey io
    def _on_hotkey_record_start(self):
        """Global hotkeys must be off while the user composes a new combo."""
        self._unregister_hotkey()
        self.settings_hint.setText("Recording — press a key combination…")

    def _on_hotkey_record_end(self):
        self._register_hotkey()
        self.settings_hint.setText("")

    def _on_hotkey_changed(self, hotkey_id, spec):
        field = self._HOTKEY_CONFIG[hotkey_id]
        if getattr(self.cfg.data, field) != spec:
            self.cfg.set(**{field: spec})
        self._reload_hotkeys()
        self._log_line(f"Hotkey '{self._HOTKEY_LABELS[hotkey_id]}' set to {spec}.")
        self._notify("AniPresence", f"{self._HOTKEY_LABELS[hotkey_id]}: {spec}")

    def _on_reset_hotkeys(self):
        defaults = {
            self._HOTKEY_ID: "Ctrl+Alt+A",
            self._HOTKEY_PAUSE_ID: "Ctrl+Alt+P",
            self._HOTKEY_CLEAR_ID: "Ctrl+Alt+X",
            self._HOTKEY_NEXT_ID: "Ctrl+Alt+N",
        }
        for hotkey_id, spec in defaults.items():
            field = self._HOTKEY_CONFIG[hotkey_id]
            self.cfg.set(**{field: spec})
            self._hotkey_buttons[hotkey_id].set_spec(spec)
        self._reload_hotkeys()
        self._log_line("Global hotkeys reset to defaults.")
        self._notify("AniPresence", "Hotkeys reset to defaults.")

    def _on_match_changed(self, value):
        self.cfg.set(min_match_score=value)

    def _on_ttl_changed(self, value):
        self.cfg.set(anilist_cache_ttl_days=value)

    def _on_anilist_sync_changed(self, enabled):
        self.cfg.set(anilist_sync_enabled=enabled)
        if enabled:
            self.controller.sync_anilist()

    def _on_anilist_token_changed(self):
        token = self.anilist_token_edit.text().strip()
        if token == self.cfg.data.anilist_access_token:
            return
        if self._login_flow:
            self._login_flow.cancel()
            self._login_flow = None
            self._login_generation += 1
            self.anilist_login_btn.setText("Connect AniList")
        self.cfg.set(anilist_access_token=token)
        self.cfg.set(anilist_sync_account="", anilist_last_sync=0)
        self.controller.anilist.set_access_token(token)
        self.controller._emit_sync_status(error="", activity="Token changed; use Sync now to verify")

    def _on_presence_option(self, field, value):
        self.cfg.set(**{field: value})
        self.controller._last_sent = None

    def _on_anilist_login(self):
        from ..anime.oauth import OAuthLogin
        if self._login_flow:
            self._login_flow.cancel()
            self._login_flow = None
            self._login_generation += 1
            self.anilist_login_btn.setText("Connect AniList")
            self.anilist_login_status.setText("Login cancelled.")
            return
        self.cfg.set(anilist_client_id=self.anilist_client_edit.text().strip())
        self._login_generation += 1
        generation = self._login_generation
        try:
            flow = OAuthLogin(self.cfg.data.anilist_client_id,
                lambda result: self.bridge.anilist_login.emit(dict(result, generation=generation)),
                client_secret=self.anilist_secret_edit.text())
        except (ValueError, OSError) as exc:
            self.anilist_login_status.setText(str(exc))
            return
        self._login_flow = flow
        self.anilist_secret_edit.clear()
        self.anilist_login_btn.setText("Cancel login")
        self.anilist_login_status.setText("Approve access in your browser. Waiting up to 3 minutes…")
        flow.start()
        if not webbrowser.open(flow.launch_url):
            flow.cancel()
            self._login_flow = None
            self.anilist_login_btn.setText("Connect AniList")
            self.anilist_login_status.setText("Could not open your browser. Try again.")

    def _on_anilist_login_result(self, result):
        if result.get('generation') != self._login_generation:
            return
        self._login_flow = None
        self.anilist_login_btn.setText("Connect AniList")
        if result.get('message'):
            self.anilist_login_status.setText(result['message'])
            return
        token, account = result['token'], result['account']
        sessions = dict(self.cfg.data.anilist_sessions)
        if self.cfg.data.anilist_access_token and self.cfg.data.anilist_sync_account:
            sessions[self.cfg.data.anilist_sync_account.casefold()] = self.cfg.data.anilist_access_token
        sessions[account.casefold()] = token
        self.cfg.set(anilist_access_token=token, anilist_sync_account=account,
                     anilist_last_sync=0, anilist_sync_enabled=True, anilist_sessions=sessions)
        self.controller.anilist.set_access_token(token)
        self.controller.add_anilist_account(account)
        self._last_library_payload = None
        self._last_calendar_payload = None
        self._library_modern.pending_edits.clear()
        self._on_library_loaded({"username": account, "sections": self.controller.watchlog.anilist_sections()})
        self._populate_anilist_accounts()
        self._load_toggles_from_config()
        self.anilist_login_status.setText(f"Connected as {account}.")
        self.controller.sync_anilist()

    def _on_anilist_disconnect(self):
        self.anilist_secret_edit.clear()
        if self._login_flow:
            self._login_flow.cancel()
            self._login_flow = None
        self._login_generation += 1
        sessions = dict(self.cfg.data.anilist_sessions)
        sessions.pop(self.cfg.data.anilist_sync_account.casefold(), None)
        self.cfg.set(anilist_access_token="", anilist_sync_account="",
                     anilist_last_sync=0, anilist_sync_enabled=False, anilist_sessions=sessions)
        self.controller.anilist.set_access_token("")
        self._load_toggles_from_config()
        self.anilist_login_btn.setText("Connect AniList")
        self.anilist_login_status.setText("Disconnected. Access can also be revoked in AniList settings.")
        self.controller._emit_sync_status(error="", activity="Disconnected")
        self._populate_anilist_accounts()

    def _on_sync_status(self, status):
        last = status.get('last_success')
        when = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(last)) if last else 'Never'
        pending = status.get('pending') or []
        lines = [f"Sync account: {status.get('account') or 'Not verified'}",
                 f"Last successful sync: {when}", status.get('activity') or 'Ready',
                 f"Pending episode updates: {len(pending)}"]
        lines.extend(pending[:8])
        if status.get('error'):
            lines.append(f"Last error: {status['error']}")
        self.sync_details.setText('\n'.join(lines))
        self.anilist_retry_btn.setEnabled(bool(self.cfg.data.anilist_access_token
                                              and self.cfg.data.anilist_sync_enabled))

    def _refresh_extension_diagnostics(self):
        d = self.controller.extension.diagnostics()
        last = time.strftime('%H:%M:%S', time.localtime(d['last_report'])) if d['last_report'] else 'Never'
        lines = [f"Bridge: {'listening' if d['running'] else 'stopped'} on port {d['port']}",
                 f"Extension: {'connected' if d['connected'] else 'not reporting'} · Last report: {last}",
                 f"Installed/worker version: {d['version'] or 'unknown'} · Required: {d['expected']}",
                 f"Open-tab readers: {', '.join(d['readers']) or 'none'}"]
        if d['rejected']:
            lines.append('Token mismatch: reload the extension or check its Options page.')
        elif d['connected'] and d['version'] != d['expected']:
            lines.append('Reload/update AniPresence Bridge in your browser’s extensions page, then refresh watching tabs.')
        elif d['connected'] and any(v != d['expected'] for v in d['readers']):
            lines.append('Old content script detected: refresh your watching tabs.')
        elif not d['connected']:
            lines.append('Enable AniPresence Bridge and open/refresh an anime tab.')
        self.extension_diagnostics.setText('\n'.join(lines))

    def _open_extension_folder(self):
        import os
        folder = (Path(sys.executable).parent / 'extension' if getattr(sys, 'frozen', False)
                  else Path(__file__).resolve().parents[2] / 'browser-extension')
        os.startfile(str(folder))

    def _on_port_changed(self, value):
        self.cfg.set(chrome_debugging_port=value)

    def _refresh_custom_sites(self):
        view = self.custom_sites_view
        view.clear()
        for entry in self._custom_sites:
            name = entry.get("name") or ""
            host = entry.get("host") or ""
            view.addItem(f"{name}  ({host})")
        if self.custom_del_btn is not None:
            self.custom_del_btn.setEnabled(view.currentRow() >= 0)

    def _on_custom_site_pick(self, _item):
        if self.custom_del_btn is not None:
            self.custom_del_btn.setEnabled(True)

    def _on_add_custom_site(self):
        name = self.custom_name_edit.text().strip()
        host = self.custom_host_edit.text().strip()
        if not name or not host:
            self._notify("AniPresence", "Enter both a site name and a domain.")
            return
        self._custom_sites.append({"name": name, "host": host})
        self._persist_custom_sites()
        self.custom_name_edit.clear()
        self.custom_host_edit.clear()
        self.custom_name_edit.setFocus()

    def _on_remove_custom_site(self):
        row = self.custom_sites_view.currentRow()
        if row < 0 or row >= len(self._custom_sites):
            return
        del self._custom_sites[row]
        self._persist_custom_sites()

    def _persist_custom_sites(self):
        self._custom_sites = self.controller.set_custom_sites(self._custom_sites)
        self._refresh_custom_sites()
        self._log_line(
            f"Custom sites: {', '.join(s['name'] for s in self._custom_sites) or 'none'}."
        )

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
            self.controller.watchlog.remove_title(item.data(Qt.UserRole + 1))
            self._refresh_stats()
            self._refresh_continue()
            self._log_line(f"Removed '{title}' from stats.")

    def _on_clear_stats(self):
        self.controller.watchlog.clear()
        self._refresh_stats()
        self._refresh_continue()
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
        self._refresh_continue()
        self.controller.refresh_activity_stats()
        self._on_recent(self.controller.recent.items())
        if message:
            self._notify("AniPresence", message)

    def _on_anilist_sync_now(self):
        self.anilist_sync_now_btn.setEnabled(False)
        self.anilist_sync_now_btn.setText("Syncing…")
        self.anilist_sync_status.setText("Contacting AniList…")
        self.controller.sync_anilist()

    def _on_anilist_synced(self, payload):
        self.anilist_sync_now_btn.setEnabled(True)
        self.anilist_sync_now_btn.setText("Sync now")
        payload = payload or {}
        message = payload.get("message") or ""
        account = payload.get("account") or ""
        titles = payload.get("titles") or 0
        if message:
            self.anilist_sync_status.setText(message)
        elif account:
            self.anilist_sync_status.setText(f"Synced {titles} titles from {account}.")
        else:
            self.anilist_sync_status.setText("Synced.")

    def _on_activity_stats(self, payload):
        if (payload.get("account") or "").casefold() != self.controller.active_anilist_account.casefold():
            return
        self._analytics.sync_button.setEnabled(not payload.get("loading"))
        self._analytics.sync_button.setText("Refreshing…" if payload.get("loading") else "Refresh AniList")
        self._analytics.sync_message = payload.get("message") or ""
        self._history_modern.sync_button.setEnabled(not payload.get("loading"))
        self._history_modern.sync_button.setText("Refreshing…" if payload.get("loading") else "Refresh AniList")
        self._history_refresh.setEnabled(not payload.get("loading"))
        self._history_refresh.setText(self._history_modern.sync_button.text())
        self._history_modern.note.setText(payload.get("message") or "Local playback + AniList feed · Feed dates are list-update dates.")
        if payload.get("loading") and not payload.get("changed"):
            return
        self._on_recent(self.controller.recent.items())
        if self.tabbar.currentIndex() == self._stats_tab_index:
            self._refresh_stats()

    def _refresh_history_feed(self):
        if not self.controller.active_anilist_account:
            self._on_import_anilist()
        else:
            self.controller.refresh_activity_stats(force=True)

    def _resume_stats_item(self, item):
        self._open_anime(item.data(Qt.UserRole + 1) or {"title": item.text()})

    def _open_anilist_entry(self, item):
        self._resume_stats_item(item)

    def _open_anime(self, entry):
        if not entry:
            return
        from ..core.media_identity import MediaIdentity
        entry = dict(entry)
        recent = self.controller.watchlog.history(self.controller.recent.items(), copy_result=False)
        resolver = MediaIdentity([entry, *recent])
        if not entry.get("episode"):
            local = next((row for row in recent if row.get("history_source") == "Local"
                          and resolver.key(row) == resolver.key(entry)), None)
            if local:
                for field in ("episode", "url", "current_time"):
                    if local.get(field) is not None:
                        entry[field] = local[field]
            elif entry.get("status") in ("CURRENT", "REPEATING") and entry.get("progress"):
                episode = int(entry["progress"])+1
                if not entry.get("episodes") or episode <= entry["episodes"]:
                    entry["episode"] = episode
        if self.stack.currentWidget() is not self._details:
            self._details.history.clear()
            self._details.entry = {}
        self._tab_anim_stop()
        self.stack.setCurrentWidget(self._details)
        self._page_heading.setVisible(self.cfg.data.ui_layout=="cinema")
        if self.cfg.data.ui_layout=="cinema":
            self._page_title.setText("Anime details")
            self._page_description.setText(entry.get("title") or "")
        self._details.open(entry)
        self._scrolling.install()

    def _watch_anime(self, entry):
        from ..core.streaming import anime_url
        if entry:
            webbrowser.open(anime_url(entry, self.cfg.data.streaming_site, self.cfg.data.streaming_search_url))

    def _on_stats_section_changed(self, index):
        self._stats_stack.setCurrentIndex(index)

    def _on_library_section_changed(self, index):
        self._library_stack.setCurrentIndex(index)
        if index == 0 and self._library_tabs.tabText(index) == "Calendar":
            self._on_refresh_library()

    def _on_refresh_library(self):
        """Kick off a live fetch of the active account's AniList list + calendar."""
        self._poster_failed.clear()
        self.library_refresh_btn.setEnabled(False)
        self.library_refresh_btn.setText("Loading…")
        self.controller.refresh_library()
        self.controller.refresh_calendar()

    def _on_library_loaded(self, payload):
        """Fill the Library sections from a background fetch."""
        if (payload.get("username") or "") != self.controller.active_anilist_account:
            return
        if payload.get("message") and getattr(self, "_last_library_payload", None) and self._last_library_payload.get("sections"):
            self._library_modern.notice.setText(payload["message"])
            self._library_modern.notice.show()
            self.library_refresh_btn.setEnabled(True)
            self.library_refresh_btn.setText("Refresh")
            return
        self._last_library_payload = payload
        self.library_refresh_btn.setEnabled(True)
        self.library_refresh_btn.setText("Refresh")
        message = payload.get("message") or ""
        username = payload.get("username") or ""
        sections = payload.get("sections") or {}
        views = {key: listw.view_state() for key, listw in self._library_section_lists.items()}
        if username:
            self.library_account_label.setText(f"{username} · AniList")
        else:
            self.library_account_label.setText("")
        self._library_modern.set_sections(sections, message)
        self._browse.sync_library()
        self._browse.library_changed()
        if self.cfg.data.ui_layout != "classic":
            self._maybe_notify_airing(sections)
            return
        for key, listw in self._library_section_lists.items():
            listw.clear()
            entries = sections.get(key) or []
            if not entries:
                if message:
                    text = message
                elif key == "Watching":
                    text = ("No AniList account set — add one in Settings and "
                            "switch to it, or press Refresh.")
                else:
                    text = "Nothing in this list yet."
                item = QListWidgetItem(text)
                item.setFlags(Qt.NoItemFlags)
                listw.addItem(item)
                continue
            for e in entries:
                e = self._artwork.enrich(e, request=False)
                title = e.get("title") or "?"
                progress = int(e.get("progress") or 0)
                episodes = e.get("episodes")
                score = e.get("mean_score")
                if episodes:
                    prog = f"Ep {progress}/{episodes}"
                else:
                    prog = f"Ep {progress}" if progress else "—"
                extra = f"  ★{score}" if score else ""
                item = QListWidgetItem(f"{title} · {prog}{extra}")
                item.setData(Qt.UserRole, e.get("url") or "")
                if not self.cfg.data.privacy_mode and e.get("url"):
                    item.setToolTip(e["url"])
                item.setData(Qt.UserRole + 1, e)
                item.setData(DETAIL_ROLE, f"{prog}{extra}")
                item.setData(Qt.UserRole + 2, e.get("keywords") or "")
                cover = e.get("cover") or ""
                if cover:
                    cached = self._cover_cache_path(cover)
                    if cached.exists():
                        item.setIcon(QIcon(str(cached)))
                    else:
                        self._library_icon_pending[cover] = item
                        if cover not in self._library_icon_fetching:
                            self._library_icon_fetching.add(cover)
                            self._nam.get(QNetworkRequest(QUrl(cover)))
                listw.addItem(item)
        self._apply_library_filter()
        for key, listw in self._library_section_lists.items():
            listw.restore_view(views[key])
        self._maybe_notify_airing(sections)

    def _on_library_changed(self, payload):
        if payload.get("account") != self.controller.active_anilist_account:
            return
        from ..core.media_identity import media_id
        from .library import SECTIONS
        panel = self._library_modern
        identity = payload.get("media_id")
        if payload.get("loading"):
            panel.pending_edits.add(identity)
            panel.notice.setText("Updating AniList…")
            panel.notice.show()
            self._browse.message.setText("Updating AniList…")
            return
        panel.pending_edits.discard(identity)
        if payload.get("error"):
            panel.notice.setText(payload["error"])
            panel.notice.show()
            self._browse.message.setText(payload["error"])
            return
        panel.notice.hide()
        cached = getattr(self, "_last_library_payload", None) or {
            "username": self.controller.active_anilist_account, "sections": self.controller.watchlog.anilist_sections()}
        cached = dict(cached, sections=cached.get("sections") or {}, message="")
        sections = {key: [e for e in rows if media_id(e) != identity] for key, rows in cached["sections"].items()}
        original = next((e for rows in cached["sections"].values() for e in rows if media_id(e) == identity), None)
        original = original or payload.get("entry") or next((e for e in self._browse.entries if media_id(e) == identity), None)
        result = payload.get("result") or {}
        if original and not result.get("removed"):
            status = result["status"]
            entry = dict(original, status=status, progress=result.get("progress", original.get("progress", 0)),
                         updated_at=result.get("updatedAt") or original.get("updated_at", 0))
            sections.setdefault(SECTIONS[status], []).append(entry)
        self._on_library_loaded(dict(cached, sections=sections))
        self._browse.message.setText("AniList library updated.")
        self._record_activity("Account", f"Library status changed to {payload['status']}", title=(original or {}).get("title"))

    def _on_library_search(self, _text):
        self._apply_library_filter()

    def _apply_library_filter(self):
        """Narrow the current status lists and calendar by the filter text."""
        query = (self.library_search.text() or "").strip().lower()
        for listw in self._library_section_lists.values():
            self._filter_list_items(listw, query)
        self._filter_calendar(query)

    def _apply_stats_filter(self, _text=None):
        query = self.stats_search.text().strip().casefold()
        for listw in [self._stats_list, *self._section_lists.values()]:
            self._filter_list_items(listw, query)

    @staticmethod
    def _filter_list_items(listw, query):
        if not query:
            for i in range(listw.count()):
                listw.item(i).setHidden(False)
            return
        for i in range(listw.count()):
            item = listw.item(i)
            searchable = f"{item.text()} {item.data(Qt.UserRole + 2) or ''}".casefold()
            item.setHidden(query.casefold() not in searchable)

    def _filter_calendar(self, query):
        self._filter_list_items(self._calendar_list, query)

    def _maybe_notify_airing(self, sections):
        """Tray-notify once per episode when a followed show airs within 24h."""
        import time

        watched = sections.get("Watching") or []
        now = time.time()
        horizon = now + 24 * 3600
        due = []
        for e in watched:
            at = e.get("next_airing_at")
            ep = e.get("next_airing_episode")
            key = (e.get("anilist_id"), ep)
            if at and now <= at <= horizon and key not in self._notified_airing:
                self._notified_airing.add(key)
                due.append((e.get("title") or "?", at))
        if not due:
            return
        due.sort(key=lambda pair: pair[1])
        names = ", ".join(t for t, _ in due[:3])
        extra = f" (+{len(due) - 3} more)" if len(due) > 3 else ""
        self._notify("Airing soon", f"{names}{extra} airs within 24h.")

    def _on_calendar_loaded(self, payload):
        """Fill the Calendar sub-tab from a background seasonal fetch."""
        if (payload.get("username") or "") != self.controller.active_anilist_account:
            return
        self._last_calendar_payload = payload
        if self.cfg.data.ui_layout != "classic" and self._library_stack.isHidden():
            return
        import datetime

        listw = self._calendar_list
        view = listw.view_state()
        listw.clear()
        self._calendar_season_label.setText(payload.get("season") or "")
        entries = payload.get("entries") or []
        message = payload.get("message") or ""
        if not entries:
            item = QListWidgetItem(message or "Nothing listed for this season.")
            item.setFlags(Qt.NoItemFlags)
            listw.addItem(item)
            return

        today = datetime.datetime.now().date()
        buckets = {}  # label -> [entries]
        for e in entries:
            at = e.get("next_airing_at")
            if at:
                day = datetime.datetime.fromtimestamp(at).date()
                delta = (day - today).days
                if delta <= 0:
                    label = "Today"
                elif delta == 1:
                    label = "Tomorrow"
                else:
                    label = day.strftime("%A, %b %d")
                sort_key = max(delta, 0)
            else:
                label = "Finished / unannounced"
                sort_key = 10 ** 9
            buckets.setdefault(label, (sort_key, []))[1].append(e)

        for label in sorted(buckets, key=lambda lbl: buckets[lbl][0]):
            header = QListWidgetItem(label)
            header.setFlags(Qt.NoItemFlags)
            listw.addItem(header)
            for e in buckets[label][1]:
                e = self._artwork.enrich(e, request=False)
                title = e.get("title") or "?"
                ep = e.get("next_airing_episode")
                prog = f"Ep {ep}" if ep else "premiering soon"
                badge = ""
                if e.get("in_library"):
                    st = (e.get("library_status") or "").upper()
                    st = "Watching" if st in ("CURRENT", "REPEATING") else st.title()
                    badge = f"  ★{st}"
                item = QListWidgetItem(f"{title} · {prog}{badge}")
                item.setData(Qt.UserRole, e.get("url") or "")
                if not self.cfg.data.privacy_mode and e.get("url"):
                    item.setToolTip(e["url"])
                item.setData(Qt.UserRole + 1, e)
                item.setData(DETAIL_ROLE, f"{prog}{badge}")
                item.setData(Qt.UserRole + 2, e.get("keywords") or "")
                cover = e.get("cover") or ""
                if cover:
                    cached = self._cover_cache_path(cover)
                    if cached.exists():
                        item.setIcon(QIcon(str(cached)))
                    else:
                        self._library_icon_pending[cover] = item
                        if cover not in self._library_icon_fetching:
                            self._library_icon_fetching.add(cover)
                            self._nam.get(QNetworkRequest(QUrl(cover)))
                listw.addItem(item)
        self._apply_library_filter()
        listw.restore_view(view)

    def _refresh_sections(self):
        """Fill the Watching/Planning/Completed lists from the imported lists."""
        sections = self.controller.watchlog.anilist_sections()
        self._section_icon_pending.clear()
        views = {key: listw.view_state() for key, listw in self._section_lists.items()}
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
                e = self._artwork.enrich(e, request=False)
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
                item.setData(DETAIL_ROLE, f"{prog} · {format_hours(secs)} watched")
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

        for key, listw in self._section_lists.items():
            listw.restore_view(views[key])

    def _refresh_stats(self, visible_only=False):
        """Re-read the controller's WatchLog and repaint the Stats tab."""
        if self.cfg.data.ui_layout=="cinema":
            if self._cinema_stats.isVisible():
                self._cinema_stats.refresh()
                self._stats_state=(id(self.controller.watchlog),self.controller.watchlog.revision)
            return
        self._stats_state = (id(self.controller.watchlog), self.controller.watchlog.revision)
        from datetime import date, timedelta
        from ..core.activity_stats import streak
        snapshot = self._analytics.refresh()
        watchlog = self.controller.watchlog
        self._stat_cards["total"].setText(format_hours(sum(e["seconds"] for e in snapshot["titles"])))
        self._stat_cards["total"].setToolTip("Measured playback plus reconciled AniList estimates; feed dates are list updates.")
        self._stat_cards["streak"].setText(f"{streak(snapshot['days'])}d")
        top = snapshot["titles"][:1]
        self._stat_cards["most"].setText(top[0]["title"] if top else "—")
        self._stat_cards["most"].setToolTip(top[0]["title"] if top else "No watch time recorded yet")
        self._stat_cards["titles"].setText(str(len(snapshot["titles"])))
        if self.cfg.data.ui_layout == "cinema" and self._cinema_stats.isVisible():
            self._cinema_stats.refresh()
        if visible_only and self.cfg.data.ui_layout != "classic" and self._stats_classic.isHidden():
            return

        if self._chart_view is not None:
            dates = [(date.today()-timedelta(days=i)).isoformat() for i in range(13,-1,-1)]
            self._update_stats_chart([(d, snapshot["days"].get(d,0)) for d in dates])

        self._refresh_sections()

        view = self._stats_list.view_state()
        self._stats_list.clear()
        self._stats_icon_pending.clear()
        titles = snapshot["titles"]
        if not titles:
            item = QListWidgetItem("No watch time recorded yet — play an anime and it'll show up here.")
            item.setFlags(Qt.NoItemFlags)
            self._stats_list.addItem(item)
            self._apply_stats_filter()
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
            item.setData(DETAIL_ROLE, " · ".join(filter(None, (
                format_hours(title.get("seconds", 0)) + " watched", f"Episode {ep}" if ep else "", website))))
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
        self._apply_stats_filter()
        self._stats_list.restore_view(view)

    def _update_stats_chart(self, days):
        """Render *days* ([(date, seconds), ...]) as a per-day bar chart."""
        data = days or []
        self._chart_days = data
        self._chart_wide = self.width() >= 1200
        if data:
            from datetime import date
            try:
                start, end = date.fromisoformat(data[0][0]), date.fromisoformat(data[-1][0])
                self._chart_caption.setText(f"WATCH ACTIVITY · {start:%b %d} – {end:%b %d}")
            except ValueError:
                self._chart_caption.setText("WATCH ACTIVITY · LAST 14 DAYS")
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
        chart.setMargins(QMargins(0, 0, 0, 0))
        chart.layout().setContentsMargins(0, 0, 0, 0)
        chart.setBackgroundRoundness(0)

        axis_x = QBarCategoryAxis()
        axis_x.append([d[5:] if self.width() >= 1200 else d[-2:] for d, _ in data])
        axis_color = QColor("#657086" if self._applied_theme == "light" else "#aaaaaa")
        axis_x.setLabelsColor(axis_color)
        axis_x.setLabelsAngle(0)
        axis_x.setGridLineVisible(False)
        chart.addAxis(axis_x, Qt.AlignBottom)
        series.attachAxis(axis_x)

        axis_y = QValueAxis()
        axis_y.setLabelFormat("%g h")
        axis_y.setRange(0, max(1, max((s / 3600.0 for _, s in data), default=0) * 1.15))
        axis_y.setTickCount(4)
        axis_y.applyNiceNumbers()
        axis_y.setLabelsColor(axis_color)
        axis_y.setGridLineColor(QColor("#d6dce9" if self._applied_theme == "light" else "#363636"))
        chart.addAxis(axis_y, Qt.AlignLeft)
        series.attachAxis(axis_y)

        chart.setAnimationOptions(QChart.NoAnimation)
        previous_chart = self._chart_view.chart()
        self._chart_view.setChart(chart)
        previous_chart.deleteLater()

    def _on_tab_changed(self, index):
        self._page_heading.setVisible(self.cfg.data.ui_layout != "classic")
        self._tab_anim_stop()
        snapshot = self.isVisible() and self.cfg.data.smooth_animations and self.stack.currentIndex() != index
        self.stack.setCurrentIndex(index)
        if self.cfg.data.ui_layout == "cinema":
            pages = {0:self._cinema_overview, self._history_tab_index:self._cinema_history,
                     self._stats_tab_index:self._cinema_stats}
            if index in pages:
                self.stack.setCurrentWidget(pages[index])
                pages[index].refresh()
        self._sync_navigation(index)
        if index == self._stats_tab_index:
            state = (id(self.controller.watchlog), self.controller.watchlog.revision)
            if state != getattr(self, "_stats_state", None):
                self._refresh_stats(visible_only=True)
            self.controller.refresh_activity_stats()
        if index == self._browse_tab_index:
            self._browse.ensure_loaded()
        if index == self._history_tab_index:
            self._on_recent(getattr(self, "_last_recent", self.controller.recent.items()))
            self.controller.refresh_activity_stats()
        if index == self._library_tab_index:
            if not getattr(self, "_last_library_payload", None):
                self._on_refresh_library()
        if index == 0:
            self._refresh_continue()
        if index == self._log_tab_index:
            self._activity_panel.refresh()
        self._animate_tab_in(self.stack.currentWidget(), snapshot)

    def _animate_tab_in(self, widget, snapshot=None):
        if not snapshot or not self.isVisible() or not self.cfg.data.smooth_animations:
            return
        from .effects import PageTransition
        overlay = PageTransition(self.stack, accent=self.cfg.data.accent)
        self._tab_overlay = overlay
        self._tab_anim = overlay.animation
        overlay.animation.finished.connect(self._tab_anim_stop)
        overlay.animation.start()

    def _tab_anim_stop(self):
        anim = getattr(self, "_tab_anim", None)
        if anim is not None:
            anim.stop()
            self._tab_anim = None
        overlay = getattr(self, "_tab_overlay", None)
        if overlay is not None:
            self._tab_overlay = None
            overlay.hide()
            overlay.deleteLater()

    def _on_recent_also_stats(self, _entries):
        """Keep the Stats tab live while it's the active tab."""
        self._refresh_continue()
        if self.tabbar.currentIndex() == self._stats_tab_index:
            self._refresh_stats()

    @staticmethod
    def _field_label(text):
        label = QLabel(text)
        label.setObjectName("sectionLabel")
        return label

    def _connect_signals(self):
        self.bridge.artwork_ready.connect(self._on_artwork_ready)
        self.bridge.chrome_status.connect(self._on_chrome_status)
        self.bridge.discord_status.connect(self._on_discord_status)
        self.bridge.detection.connect(self._on_detection)
        self.bridge.recent.connect(self._on_recent)
        self.bridge.recent.connect(self._on_recent_also_stats)
        self.bridge.stats_imported.connect(self._on_stats_imported)
        self.bridge.activity_stats.connect(self._on_activity_stats)
        self.bridge.account_avatar.connect(self._on_account_avatar)
        self.bridge.library_loaded.connect(self._on_library_loaded)
        self.bridge.library_changed.connect(self._on_library_changed)
        self.bridge.anilist_synced.connect(self._on_anilist_synced)
        self.bridge.anilist_sync_status.connect(self._on_sync_status)
        self.bridge.anilist_login.connect(self._on_anilist_login_result)
        self.bridge.update_status.connect(self._on_update_status)
        self.bridge.calendar_loaded.connect(self._on_calendar_loaded)
        self.bridge.share_tunnel.connect(self._on_tunnel_public_url)
        self.bridge.match_pending.connect(self._on_match_pending)
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
        self.cinematic_check.toggled.connect(self._on_cinematic_toggled)
        self.auto_accent_check.toggled.connect(self._on_auto_accent_toggled)
        self.hide_adult_check.toggled.connect(self._on_adult_filter_changed)
        self.anilist_combo.currentIndexChanged.connect(self._on_anilist_account_switch)
        self.interval_spin.valueChanged.connect(self._on_interval_changed)
        self.app_id_edit.editingFinished.connect(self._on_app_id_changed)
        self.anilist_sync_check.toggled.connect(self._on_anilist_sync_changed)
        self.anilist_sync_now_btn.clicked.connect(self._on_anilist_sync_now)
        self.anilist_token_edit.editingFinished.connect(self._on_anilist_token_changed)
        self.broadcast_combo.currentIndexChanged.connect(self._on_broadcast_changed)
        self.browser_combo.currentIndexChanged.connect(self._on_browser_changed)
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        self.accent_combo.currentIndexChanged.connect(self._on_accent_changed)
        self.backup_btn.clicked.connect(self._on_backup_config)
        self.win_close_btn.clicked.connect(self.close)
        self.win_min_btn.clicked.connect(self._animated_minimize)
        self.win_max_btn.clicked.connect(self._toggle_max)

        self.reconnect_btn.clicked.connect(self.controller.reconnect_discord)
        self.clear_rpc_btn.clicked.connect(self.controller.clear_rpc)
        self.open_site_btn.clicked.connect(self._open_site)
        self.open_pwa_btn.clicked.connect(self._open_pwa)
        self.open_anilist_btn.clicked.connect(self._open_anilist)
        self.fix_match_btn.clicked.connect(self._on_wrong_match)
        self.next_ep_btn.clicked.connect(self._next_episode)
        self.copy_url_btn.clicked.connect(self._copy_url)
        self.share_now_btn.clicked.connect(self._on_share_now)
        self.share_tunnel_btn.clicked.connect(self._on_share_tunnel)
        self.recent_list.itemActivated.connect(self._open_recent_item)
        self.recent_list.customContextMenuRequested.connect(self._recent_menu)

        self.match_spin.valueChanged.connect(self._on_match_changed)
        self.ttl_spin.valueChanged.connect(self._on_ttl_changed)
        self.port_spin.valueChanged.connect(self._on_port_changed)
        self.paused_check.toggled.connect(
            lambda on: self.cfg.set(show_paused_presence=on)
        )
        self.hide_no_video_check.toggled.connect(
            lambda on: self.cfg.set(hide_when_no_video=on)
        )

    def _on_adult_filter_changed(self, enabled):
        self.cfg.set(hide_adult_content=enabled)
        if self._browse.started:
            self._browse.reload()

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
        load(self.cinematic_check, self.cfg.data.cinematic_backdrop)
        with QSignalBlocker(self.cinematic_style_combo):
            self.cinematic_style_combo.setCurrentIndex(
                max(0, self.cinematic_style_combo.findData(self.cfg.data.cinematic_style))
            )
        self.cinematic_style_combo.setEnabled(self.cfg.data.cinematic_backdrop)
        load(self.auto_accent_check, self.cfg.data.auto_accent)
        load(self.hide_adult_check, self.cfg.data.hide_adult_content)
        from ..startup import is_enabled
        load(self.startup_check, self.cfg.data.start_windows and is_enabled())
        with QSignalBlocker(self.browser_combo):
            self.browser_combo.setCurrentIndex(BROWSER_INDEX[self.cfg.data.browser])
        with QSignalBlocker(self.theme_combo):
            self.theme_combo.setCurrentIndex(THEME_INDEX.get(self.cfg.data.theme, 0))
        self._sync_accent_combo()
        with QSignalBlocker(self.broadcast_combo):
            self.broadcast_combo.setCurrentIndex(BROADCAST_INDEX.get(self.cfg.data.rpc_broadcast, 0))
        with QSignalBlocker(self.match_spin):
            self.match_spin.setValue(int(self.cfg.data.min_match_score))
        with QSignalBlocker(self.ttl_spin):
            self.ttl_spin.setValue(int(self.cfg.data.anilist_cache_ttl_days))
        with QSignalBlocker(self.anilist_sync_check):
            self.anilist_sync_check.setChecked(self.cfg.data.anilist_sync_enabled)
        self.anilist_token_edit.setText(self.cfg.data.anilist_access_token)
        self.anilist_client_edit.setText(self.cfg.data.anilist_client_id)
        for field, check in self.presence_options.items():
            load(check, getattr(self.cfg.data, field))
        load(self.auto_updates_check, self.cfg.data.auto_check_updates)
        self.update_feed_edit.setText(self.cfg.data.update_feed_url)
        self._on_sync_status(self.controller.sync_status())
        self._refresh_extension_diagnostics()
        self._render_update_status()
        self.anilist_login_btn.setText("Cancel login" if self._login_flow else "Connect AniList")
        with QSignalBlocker(self.port_spin):
            self.port_spin.setValue(int(self.cfg.data.chrome_debugging_port))
        load(self.paused_check, self.cfg.data.show_paused_presence)
        load(self.hide_no_video_check, self.cfg.data.hide_when_no_video)
        for hotkey_id, btn in getattr(self, "_hotkey_buttons", {}).items():
            btn.set_spec(getattr(self.cfg.data, self._HOTKEY_CONFIG[hotkey_id], ""))

    def _after_onboarding(self):
        """Refresh widgets after the first-run wizard changes the config."""
        self.app_id_edit.setText(self.cfg.data.discord_application_id)
        self._load_toggles_from_config()
        self._populate_anilist_accounts()
        self._refresh_stats()
        self.controller.reconnect_discord()

    # ------------------------------------------------------------ handlers
    def _apply_stylesheet(self):
        self._tab_anim_stop()
        self._applied_theme = resolve_theme(self.cfg.data.theme)
        from .modern import modern_qss, revamp_qss
        from .effects import glass_qss
        self.centralWidget().configure(self.cfg.data.glass_effects, self._applied_theme, self.cfg.data.accent)
        from ..fonts import load_custom_font
        from PySide6.QtGui import QFontDatabase
        load_custom_font(self.cfg.data.font_path)
        family = self.cfg.data.font_family
        if family not in QFontDatabase.families(): family = "Figtree"
        family = family.replace("\\", "\\\\").replace('"', '\\"')
        from .modern import oled_qss, neutral_surfaces
        from .cinema import stylesheet as cinema_qss
        QApplication.instance().setStyleSheet(neutral_surfaces(
            build_theme_qss(
                self._applied_theme,
                self.cfg.data.accent,
                self.cfg.data.compact,
                self.cfg.data.mica_backdrop or self.cfg.data.cinematic_backdrop or self.cfg.data.glass_effects,
            ) + (modern_qss(self._applied_theme, self.cfg.data.accent)
                 if self.cfg.data.ui_layout == "modern" else "")
            + (glass_qss(self._applied_theme, self.cfg.data.accent) if self.cfg.data.glass_effects else "")
            + (revamp_qss(self._applied_theme,self.cfg.data.accent,self.cfg.data.glass_effects)
               if self.cfg.data.ui_layout == "modern" else "")
            + (oled_qss() if self._applied_theme == "oled" else "")
            + f'\nQWidget {{ font-family: "{family}"; }}',
            self._applied_theme, self.cfg.data.accent)
            + (cinema_qss(self._applied_theme) if self.cfg.data.ui_layout == "cinema" else "")
        )
        self._page_heading.accent = self.cfg.data.accent
        self._page_heading.light = self._applied_theme == "light"
        self._page_heading.scene = self.cfg.data.artwork_theme
        self._page_heading.update()
        self._sidebar.update()
        self._brand.configure(self.cfg.data.accent, self._applied_theme)
        self._continue_frame.configure(self.cfg.data.accent)
        self._library_modern.configure(self._applied_theme == "light")
        self._sync_navigation(self.tabbar.currentIndex())
        self._scrolling.install()
        if getattr(self, "_chart_view", None) is not None and hasattr(self, "_chart_days"):
            self._update_stats_chart(self._chart_days)
        if self._cover_spinner is not None:
            self._cover_spinner.set_color(self.cfg.data.accent)
        self._apply_backdrop()
        self._update_ambient()

    def _on_glass_changed(self, enabled):
        self.cfg.set(glass_effects=enabled)
        self._apply_stylesheet()

    def _on_animations_changed(self, enabled):
        self.cfg.set(smooth_animations=enabled)
        if not enabled:
            self._tab_anim_stop()
            if self._progress_animation.state() == QAbstractAnimation.Running:
                target = self._progress_animation.endValue()
                self._progress_animation.stop()
                self.progress.setValue(target)

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

    def _on_cinematic_toggled(self, on):
        self.cfg.set(cinematic_backdrop=on)
        self.cinematic_style_combo.setEnabled(on)
        self._apply_stylesheet()
        self._update_ambient()

    def _on_cinematic_style_changed(self, index):
        style = self.cinematic_style_combo.itemData(index) or "frosted"
        if self.cfg.data.cinematic_style != style:
            self.cfg.set(cinematic_style=style)
        self._ambient_src_version += 1  # drop the cached render for the old style
        self._update_ambient()

    def _on_auto_accent_toggled(self, on):
        self.cfg.set(auto_accent=on)
        if on:
            self._maybe_auto_accent()

    def _on_theme_changed(self, index):
        theme = THEME_VALUE.get(index, "dark")
        self.cfg.set(theme=theme)
        self._apply_stylesheet()

    def _on_compact_toggled(self, on):
        self.cfg.set(compact=on)
        self._apply_stylesheet()
        self._rebuild_ui()

    def _on_accent_changed(self, index):
        data = self.accent_combo.itemData(index)
        if data == CUSTOM_ACCENT:
            self._on_pick_accent()
            return
        if data:
            self.cfg.set(artwork_theme=self.accent_combo.itemText(index))
            self._apply_accent(data)

    def _apply_accent(self, color):
        """Pin a fixed accent (preset or picked) and turn auto-accent off."""
        self.cfg.set(accent=color, auto_accent=False)
        if self.auto_accent_check is not None:
            self.auto_accent_check.setChecked(False)
        self.setWindowIcon(app_icon(64, color))
        if self._tray:
            self._tray_base_pm = None
            self._tray.setIcon(app_icon(64, color))
            self._update_tray_icon(self._tray_fraction)
        self._apply_stylesheet()
        self._sync_accent_combo()

    def _sync_accent_combo(self):
        """Match the header preset dropdown to the current accent color."""
        combo = getattr(self, "accent_combo", None)
        if combo is None:
            return
        current = str(self.cfg.data.accent or "").lower()
        idx = combo.findData(current)
        if idx < 0:
            idx = combo.findData(CUSTOM_ACCENT)
            if idx < 0:
                return
        with QSignalBlocker(combo):
            combo.setCurrentIndex(idx)

    def _on_pick_accent(self):
        current = QColor(self.cfg.data.accent)
        if not current.isValid():
            current = QColor("#7c5cff")
        color = QColorDialog.getColor(current, self, "Choose accent color")
        if color.isValid():
            self._apply_accent(color.name())

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
        self._history_state = None
        self._stats_state = None
        if getattr(self, "_rebuilding", False):
            return
        self._rebuilding = True
        try:
            self._do_rebuild()
        finally:
            self._rebuilding = False

    def _do_rebuild(self):
        current_tab = self.tabbar.currentIndex()
        detail_entry = dict(self._details.entry) if self.stack.currentWidget() is self._details else None
        detail_history = list(self._details.history)
        self._details.generation += 1
        log_text = self.log_view.toPlainText()
        login_status = self.anilist_login_status.text()
        draft_fields = {name: getattr(self, name).text() for name in (
            "anilist_secret_edit", "anilist_client_edit", "anilist_token_edit", "tmdb_edit",
            "update_feed_edit", "app_id_edit", "stats_search", "library_search")}
        chips = [(chip.text(), chip.objectName()) for chip in (self.chrome_chip, self.discord_chip)]
        self._tab_anim_stop()
        for sig in (self.bridge.chrome_status, self.bridge.discord_status,
                    self.bridge.detection, self.bridge.recent, self.bridge.log,
                    self.bridge.stats_imported, self.bridge.activity_stats, self.bridge.account_avatar, self.bridge.artwork_ready,
                    self.bridge.library_loaded, self.bridge.library_changed, self.bridge.anilist_synced,
                    self.bridge.anilist_sync_status, self.bridge.anilist_login,
                    self.bridge.update_status, self.bridge.calendar_loaded,
                    self.bridge.share_tunnel, self.bridge.match_pending):
            try:
                sig.disconnect()
            except (TypeError, RuntimeError):
                pass
        self._build_ui()
        self._connect_signals()
        self._scrolling.install()
        self._populate_anilist_accounts()
        self._load_toggles_from_config()
        self.log_view.setPlainText(log_text)
        self.anilist_login_status.setText(login_status)
        for name, text in draft_fields.items():
            with QSignalBlocker(getattr(self, name)):
                getattr(self, name).setText(text)
        for chip, (text, object_name) in zip((self.chrome_chip, self.discord_chip), chips):
            chip.setText(text)
            chip.setObjectName(object_name)
            chip.style().unpolish(chip)
            chip.style().polish(chip)
        self.tabbar.setCurrentIndex(current_tab)
        if getattr(self, "_last_recent", None) is not None:
            self._on_recent(self._last_recent)
        if self._current_payload:
            self._on_detection(getattr(self, "_raw_detection_payload", self._current_payload))
        else:
            self.show_placeholder()
        self._update_cover_size()
        self._update_ambient()
        self._refresh_continue()
        self._update_preview()
        if self.tabbar.currentIndex() == self._stats_tab_index:
            self._refresh_stats()
        if detail_entry:
            self._open_anime(detail_entry)
            self._details.history = detail_history

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
        if hasattr(self,"_cinema_account"):
            self._cinema_account.setText(active or "Account")
        if hasattr(self, "_history_modern"):
            self._on_recent(self.controller.recent.items())
        if hasattr(self,"_sidebar_account"):
            connected=bool(self.cfg.data.anilist_access_token and self.cfg.data.anilist_sync_account==active)
            self._sidebar_account.setText(f"{active or 'Default profile'}\nAniList · {'Connected' if connected else 'Public' if active else 'Add account'}" if self.width() >= 900 else "")
            self._sidebar_account.setToolTip(f"{active or 'Default profile'} · Switch or connect accounts")

    def _load_account_avatar(self):
        """Request the avatar for the currently active account (monogram while
        it loads, cleared on the shared Default profile)."""
        name = self.controller.active_anilist_account or ""
        self._account_avatar_url = ""
        self._set_account_avatar_placeholder(name)
        if not name:
            return
        self.controller.fetch_account_avatar(name)

    def _set_account_avatar_placeholder(self, name):
        pixmap = QPixmap(34, 34)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(self.cfg.data.accent))
        painter.drawEllipse(0, 0, 34, 34)
        painter.setPen(Qt.white)
        painter.drawText(pixmap.rect(), Qt.AlignCenter, name[:1].upper() or "A")
        painter.end()
        self.account_avatar.setPixmap(pixmap)
        if hasattr(self, "_sidebar_account"):
            self._sidebar_account.setIcon(QIcon(pixmap))

    def _on_account_avatar(self, username, url):
        if username != self.controller.active_anilist_account:
            return  # switched away while the lookup was in flight
        self._account_avatar_url = url or ""
        if not url:
            self._set_account_avatar_placeholder(username)
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
        if hasattr(self, "_sidebar_account"):
            self._sidebar_account.setIcon(QIcon(round_pm))

    def _on_anilist_account_switch(self, index):
        name = self.anilist_combo.itemData(index) or ""
        if name != self.controller.active_anilist_account:
            self.controller.switch_anilist_account(name)
            self._last_library_payload = None
            self._last_calendar_payload = None
            self._library_modern.reset_filters()
            self._library_modern.pending_edits.clear()
            self._library_modern.notice.hide()
            self._on_library_loaded({"username": name, "message": "Loading library…"})
            self._calendar_list.clear()
            self._refresh_stats()
        self._populate_anilist_accounts()
        self._load_toggles_from_config()
        self._analytics.sync_button.setEnabled(True)
        self._analytics.sync_button.setText("Refresh AniList")
        self._analytics.sync_message = ""
        self._history_modern.sync_button.setEnabled(True)
        self._history_modern.sync_button.setText("Refresh AniList")
        self._history_refresh.setEnabled(True)
        self._history_refresh.setText("Refresh AniList")
        self._history_modern.note.setText("Local playback + AniList feed · Feed dates are list-update dates.")
        self.controller.refresh_activity_stats()
        self._on_refresh_library()

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
        self._raw_detection_payload = payload
        payload = self._artwork.enrich(payload, episode=True, priority=0)
        previous = self._current_payload or {}
        self._current_payload = payload
        self._load_banner(payload.get("banner_url") or "")
        key = (payload.get("url"),payload.get("title"),payload.get("episode"),bool(payload.get("paused")))
        if key != self._activity_playback_key:
            self._activity_playback_key = key
            self._record_activity("Playback", "Paused" if payload.get("paused") else "Started watching",
                                  title=payload.get("title"),episode=payload.get("episode"))
        if self._match_panel_url and self._match_panel_url != payload.get("url"):
            self._hide_match_chooser()
        title = payload.get("title") or "Unknown title"
        self.anime_title.setText(title)
        self._refresh_overview_logo()

        parts = []
        if payload.get("episode"):
            parts.append(
                format_episode_progress(payload["episode"], payload.get("episodes_total"))
            )
        airing = next_airing_label(
            payload.get("next_airing_at"),
            payload.get("next_airing_episode"),
            payload.get("episode"),
        )
        if airing and not payload.get("paused"):
            parts.append(airing)
        season = format_season(payload.get("season"), payload.get("season_year"))
        if season:
            parts.append(season)
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
            target = int(max(0, min(1000, 1000 * current / duration)))
        else:
            target = 0
        self._progress_animation.stop()
        if (self.cfg.data.smooth_animations and self.progress.isVisible()
                and previous.get("url") == payload.get("url") and previous
                and abs(target - self.progress.value()) < 150):
            self._progress_animation.setStartValue(self.progress.value())
            self._progress_animation.setEndValue(target)
            self._progress_animation.start()
        else:
            self.progress.setValue(target)

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
            frac = (current / duration) if duration > 0 else None
            self._tray_fraction = frac
            self._update_tray_icon(frac)
            tip = f"AniPresence · {title}"
            if payload.get("episode"):
                tip += f" · Ep {payload['episode']}"
            if duration > 0:
                tip += f" · {format_seconds(current)} / {format_seconds(duration)}"
            self._tray.setToolTip(tip)

        cover = payload.get("cover_url") or ""
        if cover and cover != self._cover_url:
            self._cover_url = cover
            self._request_cover(cover)
        elif self._cover_pixmap and cover:
            self.cover_label.setPixmap(self._cover_pixmap)

        self._maybe_auto_accent()
        self._refresh_continue()
        self._update_preview()

    # --------------------------------------------------------------- preview
    def _build_preview(self, parent_layout):
        """A live mockup of the Discord activity card (updates in real time)."""
        frame = QFrame()
        frame.setObjectName("preview")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(self._pad, self._pad, self._pad, self._pad)
        lay.setSpacing(9)

        head = QHBoxLayout()
        self.preview_dot = QLabel("\u25cf")
        self.preview_dot.setObjectName("previewDot")
        head.addWidget(self.preview_dot)
        self.preview_app = QLabel("AniPresence")
        self.preview_app.setObjectName("previewApp")
        head.addWidget(self.preview_app)
        head.addStretch(1)
        self.preview_status = QLabel("Idle")
        self.preview_status.setObjectName("previewStatus")
        head.addWidget(self.preview_status)
        lay.addLayout(head)

        self.preview_title = QLabel("Nothing playing")
        self.preview_title.setObjectName("previewTitle")
        self.preview_title.setWordWrap(True)
        lay.addWidget(self.preview_title)

        self.preview_state = QLabel("Play an anime to preview your presence.")
        self.preview_state.setObjectName("previewState")
        self.preview_state.setWordWrap(True)
        lay.addWidget(self.preview_state)

        foot = QHBoxLayout()
        self.preview_elapsed = QLabel("")
        self.preview_elapsed.setObjectName("previewTime")
        foot.addWidget(self.preview_elapsed)
        foot.addStretch(1)
        self.preview_buttons = QLabel("Watch Anime · Open AniList")
        self.preview_buttons.setObjectName("previewButtons")
        foot.addWidget(self.preview_buttons)
        lay.addLayout(foot)

        frame.setMinimumHeight(96)
        parent_layout.addWidget(frame)
        self._preview_frame = frame

    def _update_preview(self):
        """Refresh the mock Discord card to mirror the live controller state."""
        if not getattr(self, "preview_title", None):
            return
        payload = self._current_payload
        if not payload:
            self.preview_status.setText("Idle")
            self.preview_title.setText("Nothing playing")
            self.preview_state.setText("Play an anime to preview your presence.")
            self.preview_elapsed.setText("")
            self.preview_buttons.setText("")
            return

        title = payload.get("title") or "Unknown title"
        self.preview_title.setText(f"Watching: {title}")
        duration = float(payload.get("duration") or 0)
        current = float(payload.get("current_time") or 0)
        paused = bool(payload.get("paused"))
        parts = []
        if payload.get("episode"):
            parts.append(
                format_episode_progress(payload["episode"], payload.get("episodes_total"))
            )
        airing = next_airing_label(
            payload.get("next_airing_at"),
            payload.get("next_airing_episode"),
            payload.get("episode"),
        )
        if airing and not paused:
            parts.append(airing)
        if duration > 0:
            parts.append(f"{format_seconds(current)} / {format_seconds(duration)}")
        if payload.get("website"):
            parts.append(f"on {payload['website']}")
        self.preview_status.setText("Paused" if paused else "Playing")
        if paused and parts:
            parts = ["Paused"] + parts
        self.preview_state.setText(" · ".join(parts) or "Watching")

        start = payload.get("session_start")
        if start and not paused:
            elapsed = max(0, int(time.time() - start))
            self.preview_elapsed.setText(f"{format_seconds(elapsed)} elapsed")
        else:
            self.preview_elapsed.setText("")

        buttons = []
        if payload.get("url"):
            buttons.append("Watch Anime")
        if payload.get("anilist_url"):
            buttons.append("Open AniList")
        self.preview_buttons.setText("  ·  ".join(buttons))

    def _tick_preview_elapsed(self):
        """Keep the preview's elapsed clock live while something plays."""
        if not getattr(self, "preview_elapsed", None) or not self._current_payload:
            return
        if self._current_payload.get("paused"):
            return
        start = self._current_payload.get("session_start")
        if not start:
            return
        elapsed = max(0, int(time.time() - start))
        self.preview_elapsed.setText(f"{format_seconds(elapsed)} elapsed")

    # ------------------------------------------------------------- ambient
    def _maybe_auto_accent(self):
        """Sample the current cover art for an accent color (when enabled)."""
        if not self.cfg.data.auto_accent:
            return
        if not self._cover_pixmap or self._cover_pixmap.isNull():
            return
        color = _sample_accent_color(self._cover_pixmap)
        if not color:
            return
        if color.lower() == str(self.cfg.data.accent or "").lower():
            return
        self.cfg.set(accent=color)
        if self._cover_spinner is not None:
            self._cover_spinner.set_color(color)
        self.setWindowIcon(app_icon(64, color))
        if self._tray:
            self._tray_base_pm = None
            self._tray.setIcon(app_icon(64, color))
            self._update_tray_icon(self._tray_fraction)
        self._sync_accent_combo()
        self._apply_stylesheet()

    def _update_ambient(self):
        """Fill the window behind the UI with the cover (cinematic backdrop).

        Either style blurs the cover into a frosted full-bleed wall, or paints
        a color gradient sampled from it; both run under the title bar so the
        top of the window is never a flat black slab. Renders are cached per
        window size and style so resizing is cheap.
        """
        self._sidebar.update()
        self._page_heading.update()
        ambient = self._ambient
        if ambient is None:
            return
        if not self.cfg.data.cinematic_backdrop or self._applied_theme != "light":
            ambient.hide()
            return
        source = self._cover_pixmap
        if source is None or source.isNull():
            ambient.hide()
            return
        w = self.width()
        h = self.height()
        if w <= 0 or h <= 0:
            ambient.hide()
            return
        dpr = self.devicePixelRatio() or 1.0
        style = self.cfg.data.cinematic_style
        key = (w, h, dpr, self._ambient_src_version, style)
        if key != getattr(self, "_ambient_cache_key", None):
            self._ambient_cache_key = key
            tw, th = int(w * dpr), int(h * dpr)
            ambient.setPixmap(self._render_ambient(source, tw, th, style))
        ambient.setGeometry(0, 0, w, h)
        ambient.lower()
        ambient.show()

    def _render_ambient(self, source, tw, th, style="frosted"):
        """Draw the cinematic backdrop in the selected style.

        All styles draw onto a ``tw x th`` device-pixel canvas that covers the
        whole window (title bar included):

        * ``frosted``   — moderate blur, full-bleed, milky-white veil.
        * ``dreamy``    — very aggressive blur into a soft pastel wash.
        * ``gradient``  — vertical fade between the cover's top/bottom tones.
        * ``diagonal``  — diagonal fade from top-left to bottom-right tones.
        * ``vignette``  — blurred cover with a dark radial vignette at the edges.
        * ``duotone``   — blurred monochrome cover tinted with the accent color.

        The blurred variants collapse then re-expand the source (a cheap
        gaussian-style blur) so a small portrait never looks jagged. The layer
        is dimmed further by the ambient opacity effect, keeping text legible.
        """
        if style == "gradient":
            return self._flat_gradient(tw, th, _ambient_gradient_colors(source))
        if style == "diagonal":
            return self._flat_gradient(tw, th, _ambient_diagonal_colors(source), diagonal=True)
        if style == "duotone":
            return self._monochrome_tint(source, tw, th)
        if style == "dreamy":
            return self._blur_veil(source, tw, th, scale=5, veil=QColor(255, 255, 255, 110))
        if style == "vignette":
            return self._blur_veil(source, tw, th, scale=8, veil=None, vignette=True)

        return self._blur_veil(source, tw, th, scale=10, veil=QColor(255, 255, 255, 60))

    def _flat_gradient(self, tw, th, colors, diagonal=False):
        """Canvas filled with a linear gradient across the two sampled colors."""
        top, bottom = colors
        out = QPixmap(tw, th)
        out.fill(Qt.black)
        p = QPainter(out)
        grad = QLinearGradient(0, 0, tw if diagonal else 0, th if diagonal else th)
        grad.setColorAt(0.0, top)
        grad.setColorAt(1.0, bottom)
        p.fillRect(out.rect(), QBrush(grad))
        p.end()
        return out

    def _blur_veil(self, source, tw, th, scale, veil, vignette=False):
        """Blur the cover full-bleed (``scale`` = blur strength), then overlay a
        color veil and/or a dark radial vignette."""
        out = QPixmap(tw, th)
        out.fill(Qt.black)
        p = QPainter(out)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        small = source.scaled(
            max(1, tw // scale), max(1, th // scale),
            Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation,
        )
        src = small.scaled(tw, th, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        x = (src.width() - tw) // 2
        y = (src.height() - th) // 2
        p.drawPixmap(0, 0, src, x, y, tw, th)
        if veil is not None:
            p.fillRect(out.rect(), veil)
        if vignette:
            grad = QRadialGradient(tw / 2, th / 2, max(tw, th) * 0.75)
            grad.setColorAt(0.0, QColor(0, 0, 0, 0))
            grad.setColorAt(0.75, QColor(0, 0, 0, 40))
            grad.setColorAt(1.0, QColor(0, 0, 0, 170))
            p.fillRect(out.rect(), QBrush(grad))
        p.end()
        return out

    def _monochrome_tint(self, source, tw, th):
        """Blur the cover, drain it to grayscale, then tint it with the accent."""
        out = QPixmap(tw, th)
        out.fill(Qt.black)
        p = QPainter(out)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        try:
            img = source.toImage().convertToFormat(QImage.Format_ARGB32).scaled(
                max(1, tw // 6), max(1, th // 6),
                Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation,
            ).convertToFormat(QImage.Format_Grayscale8)
            big = img.scaled(
                tw, th, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
            ).convertToFormat(QImage.Format_ARGB32)
            src = QPixmap.fromImage(big)
            x = (src.width() - tw) // 2
            y = (src.height() - th) // 2
            p.drawPixmap(0, 0, src, x, y, tw, th)
            accent = QColor(self.cfg.data.accent)
            if accent.isValid():
                p.setCompositionMode(QPainter.CompositionMode_SoftLight)
                p.fillRect(out.rect(), accent)
                p.setCompositionMode(QPainter.CompositionMode_SourceOver)
            p.fillRect(out.rect(), QColor(0, 0, 0, 60))  # moody depth
        except Exception:  # noqa: BLE001 - fall back to the frosted look
            p.end()
            return self._blur_veil(source, tw, th, scale=10, veil=QColor(255, 255, 255, 60))
        p.end()
        return out

    # -------------------------------------------------------------- tray
    def _tray_base_pixmap(self):
        """The clean 64px logo (no ring) used to render progress overlays."""
        if self._tray_base_pm is not None:
            return self._tray_base_pm
        try:
            from ..logo import logo_pixmap
            pm = logo_pixmap(64)
            if pm is not None:
                self._tray_base_pm = pm
                return pm
        except Exception:  # noqa: BLE001
            pass
        pm = QPixmap(64, 64)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(self.cfg.data.accent))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(0, 0, 64, 64, 14, 14)
        p.setPen(QPen(QColor("#ffffff")))
        font = p.font()
        font.setPixelSize(26)
        font.setBold(True)
        p.setFont(font)
        p.drawText(pm.rect(), Qt.AlignCenter, "AP")
        p.end()
        self._tray_base_pm = pm
        return pm

    def _update_tray_icon(self, fraction=None):
        """Draw a progress ring around the logo for the tray icon."""
        if self._tray is None:
            return
        base = self._tray_base_pixmap()
        pm = QPixmap(base.size())
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.drawPixmap(0, 0, base)
        if fraction and 0 < fraction < 1:
            pen = QPen(QColor(self.cfg.data.accent), 4)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            m = 3.0
            rect = QRectF(m, m, base.width() - 2 * m, base.height() - 2 * m)
            p.drawArc(rect, 90 * 16, int(-fraction * 360 * 16))
        p.end()
        self._tray.setIcon(QIcon(pm))

    def _reset_tray_icon(self):
        """Drop any progress ring and restore the plain logo."""
        if self._tray is None:
            return
        self._tray_fraction = None
        self._tray.setIcon(app_icon(64, self.cfg.data.accent))

    def _on_recent(self, entries):
        self._last_recent = entries
        state = (id(self.controller.watchlog), self.controller.watchlog.revision, entries)
        if state == getattr(self, "_history_state", None):
            return
        self._history_state = state
        if self.cfg.data.ui_layout=="cinema":
            if self._cinema_history.isVisible():self._cinema_history.refresh()
            return
        entries = self.controller.watchlog.history(entries, copy_result=False)
        self._history_modern.set_entries(entries, ordered=True)
        if self.cfg.data.ui_layout == "cinema" and self._cinema_history.isVisible():
            self._cinema_history.refresh()
        view = self.recent_list.view_state()
        self._recent_icon_pending.clear()
        self.recent_list.clear()
        if not entries:
            item = QListWidgetItem("Nothing watched yet — play an anime in your browser.")
            item.setFlags(Qt.NoItemFlags)
            self.recent_list.addItem(item)
            return
        for entry in (entries[:250] if self.cfg.data.ui_layout != "classic" else entries):
            title = entry.get("title", "?")
            ep = entry.get("episode")
            site = entry.get("website", "")
            label = f"{title} · Ep {ep} · {site}" if ep else f"{title} · {site}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, entry)
            item.setData(Qt.UserRole + 1, entry)
            source = "AniList feed · List-update date" if entry.get("history_source") == "AniList feed" else site
            item.setData(DETAIL_ROLE, " · ".join(filter(None, (entry.get("activity_status") or (f"Episode {ep}" if ep else ""), entry.get("episode_title"), source))))
            if not self.cfg.data.privacy_mode:
                item.setToolTip(entry.get("url") or "")
            cover = entry.get("cover") or ""
            if cover and self.cfg.data.ui_layout == "classic":
                cached = self._cover_cache_path(cover)
                if cached.exists():
                    item.setIcon(QIcon(str(cached)))
                else:
                    self._recent_icon_pending[cover] = item
                    if cover not in self._recent_icon_fetching:
                        self._recent_icon_fetching.add(cover)
                        self._nam.get(QNetworkRequest(QUrl(cover)))
            self.recent_list.addItem(item)

        self.recent_list.restore_view(view)

    def _open_recent_item(self, item):
        self._open_anime(item.data(Qt.UserRole + 1) or item.data(Qt.UserRole) or {})

    def _recent_menu(self, pos, listw=None):
        listw = self.recent_list if listw is None else listw
        item = listw.itemAt(pos)
        if item is None:
            return
        entry = item.data(Qt.UserRole + 1) or {}
        url = entry.get("url")
        if not entry:
            return
        menu = QMenu(self)
        act_open = menu.addAction("Open in browser")
        act_pwa = menu.addAction("Open as PWA")
        act_open.setEnabled(bool(url))
        act_pwa.setEnabled(bool(url))
        menu.addSeparator()
        act_remove = menu.addAction("Remove from history")
        choice = menu.exec(listw.viewport().mapToGlobal(pos))
        if choice is act_open:
            self._open_anime(entry)
        elif choice is act_pwa:
            self.controller.launch_pwa(url)
        elif choice is act_remove:
            self.controller.remove_recent_entry(entry)

    def _on_clear_history(self):
        self.controller.clear_recent()
        self._log_line("Watch history cleared.")

    def _on_clear_log(self):
        self.log_view.clear()
        self._activity.clear()
        self._activity_panel.refresh()

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
        """Check a release feed in the background; results are delivered via Qt."""
        import threading
        from ..updates import check_release, version_tuple
        if self._update_busy:
            return
        feed = (self.cfg.data.update_feed_url or "").strip()
        if not feed:
            self._on_update_status({'stage': 'error', 'message': 'Set a release feed URL in Settings → App updates.'})
            return
        self._update_busy = True
        self._on_update_status({'stage': 'checking', 'message': 'Checking for updates…'})

        def run():
            try:
                release = check_release(feed)
                if version_tuple(release['version']) > version_tuple(__version__):
                    suffix = '' if release['url'] else ' (installer not attached to release yet)'
                    result = {'stage': 'available', 'release': release,
                              'message': f"Version {release['version']} is available{suffix}."}
                else:
                    result = {'stage': 'current', 'message': f"You're up to date ({__version__})."}
            except Exception as exc:
                result = {'stage': 'error', 'message': f'Update check failed: {exc}'}
            self.bridge.update_status.emit(result)
        threading.Thread(target=run, daemon=True, name='update-check').start()

    def _auto_check_updates(self):
        if self.cfg.data.auto_check_updates and self._update_state.get('stage') != 'downloaded':
            self._check_for_updates()

    def _on_auto_updates_changed(self, enabled):
        self.cfg.set(auto_check_updates=enabled)
        if enabled:
            self._auto_check_updates()

    def _on_update_status(self, result):
        self._update_state = result
        if result.get('stage') not in ('checking', 'downloading'):
            self._update_busy = False
        self._render_update_status()
        if result.get('stage') == 'available':
            self._notify('AniPresence update', result['message'] + ' Download it in Settings → App updates.')

    def _render_update_status(self):
        state = self._update_state
        self.update_status_label.setText(state.get('message') or '')
        self.update_check_btn.setEnabled(not self._update_busy)
        self.update_download_btn.setEnabled(not self._update_busy and bool((state.get('release') or {}).get('url')))
        self.update_install_btn.setEnabled(state.get('stage') == 'downloaded')
        self._update_banner_label.setText(state.get('message') or '')
        self._update_banner.setVisible(state.get('stage') in ('available', 'downloaded'))

    def _show_update_settings(self):
        self.tabbar.setCurrentIndex(self._settings_tab_index)
        self._updates_section.expand()
        self._settings_page.ensureWidgetVisible(self.update_status_label, 0, 50)

    def _download_update(self):
        import threading
        from ..updates import download_release
        release = self._update_state.get('release')
        if self._update_busy or not release or not release.get('url'):
            return
        self._update_busy = True
        self._on_update_status({'stage': 'downloading', 'message': 'Downloading installer…', 'release': release})
        def run():
            try:
                path = download_release(release, APP_DIR / 'updates', lambda percent:
                    self.bridge.update_status.emit({'stage': 'downloading', 'release': release,
                                                    'message': f'Downloading installer… {percent}%'}))
                result = {'stage': 'downloaded', 'release': release, 'path': str(path),
                          'message': f"Version {release['version']} downloaded. Click Install update to launch setup."}
            except Exception as exc:
                result = {'stage': 'error', 'release': release, 'message': f'Download failed: {exc}'}
            self.bridge.update_status.emit(result)
        threading.Thread(target=run, daemon=True, name='update-download').start()

    def _install_update(self):
        import os
        path = self._update_state.get('path')
        if self._update_state.get('stage') != 'downloaded' or not path:
            return
        try:
            os.startfile(path)
        except OSError as exc:
            self.update_status_label.setText(f'Could not launch installer: {exc}')
            return
        self._quit_app()

    def _on_log(self, line):
        from ..core.activity import classify_message
        text = redact_urls(line) if self.cfg.data.privacy_mode else line
        self.log_view.appendPlainText(text)
        self._record_activity(classify_message(text), text)

    def _record_activity(self, kind, message, **context):
        self._activity.add(kind, message, account=self.controller.active_anilist_account, **context)
        if hasattr(self,"_activity_panel") and self.tabbar.currentIndex() == self._log_tab_index:
            self._activity_panel.refresh()

    def _log_line(self, message):
        self._on_log(message)

    # ------------------------------------------------------------ cover art
    def _load_banner(self, url):
        if url == self._banner_url and not self._page_heading.art.isNull():return
        self._banner_url = url
        self._page_heading.art = QPixmap()
        self._page_heading.update()
        self._sidebar.update()
        if not url:
            self._page_heading.art = QPixmap(); self._page_heading.update(); return
        path=self._cover_cache_path(url)
        if path.exists():
            self._page_heading.art=QPixmap(str(path));self._page_heading.update();self._sidebar.update()
        elif url not in self._banner_requests:
            self._banner_requests.add(url)
            self._nam.get(QNetworkRequest(QUrl(url)))

    def _cover_cache_path(self, url):
        if url in self._cover_paths:
            return self._cover_paths[url]
        ext = Path(urlsplit(url).path).suffix.lower() or ".jpg"
        name = hashlib.sha1(url.encode("utf-8", "ignore")).hexdigest()[:20] + ext
        if len(self._cover_paths) >= 8192:
            self._cover_paths.clear()
        path = self._cover_cache_dir / name
        self._cover_paths[url] = path
        return path

    def _request_cover(self, url):
        cached = self._cover_cache_path(url)
        if cached.exists():
            pixmap = QPixmap(str(cached))
            if not pixmap.isNull():
                self._cover_pixmap = pixmap
                self._ambient_src_version += 1
                self.cover_label.setPixmap(pixmap)
                self._cover_spinner.hide()
                self._maybe_auto_accent()
                self._update_ambient()
                return
        self._cover_requested = True
        self._cover_spinner.show()
        request = QNetworkRequest(QUrl(url))
        self._nam.get(request)

    def _request_library_poster(self, url):
        if self._cover_cache_path(url).exists():
            return
        if url in self._poster_fetching or url in self._poster_failed or len(self._poster_fetching) >= 6:
            return
        self._poster_fetching.add(url)
        request = QNetworkRequest(QUrl(url))
        request.setAttribute(QNetworkRequest.User, "library-poster")
        request.setTransferTimeout(15000)
        self._nam.get(request)

    def _on_cover_reply(self, reply):
        orig = reply.request().url().toString()
        if reply.request().attribute(QNetworkRequest.User) == "library-poster":
            self._poster_fetching.discard(orig)
            from PySide6.QtCore import QBuffer, QIODevice
            from PySide6.QtGui import QImageReader
            data = bytes(reply.readAll()) if reply.error() == QNetworkReply.NoError else b""
            buffer = QBuffer();buffer.setData(data);buffer.open(QIODevice.ReadOnly)
            if data and QImageReader(buffer).canRead():
                try:
                    self._cover_cache_dir.mkdir(parents=True, exist_ok=True)
                    self._cover_cache_path(orig).write_bytes(data)
                except OSError:
                    self._poster_failed.add(orig)
            else:
                self._poster_failed.add(orig)
            self._images.invalidate_missing(self._cover_cache_path(orig))
            self._queue_artwork_repaint()
            reply.deleteLater()
            return
        if orig in self._banner_requests:
            self._banner_requests.discard(orig)
            if reply.error() == QNetworkReply.NoError:
                data=bytes(reply.readAll()); pixmap=QPixmap()
                if pixmap.loadFromData(data):
                    try:
                        self._cover_cache_dir.mkdir(parents=True,exist_ok=True)
                        self._cover_cache_path(orig).write_bytes(data)
                    except OSError: pass
                    if orig == self._banner_url:
                        self._page_heading.art=pixmap; self._page_heading.update(); self._sidebar.update()
            reply.deleteLater()
            return
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
                if orig == self._account_avatar_url:
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
        elif orig in self._library_icon_pending:
            # Library-tab cover.
            self._library_icon_fetching.discard(orig)
            item = self._library_icon_pending.get(orig)
            pending = self._library_icon_pending
            pending.pop(orig, None)
        elif orig in self._continue_icon_pending:
            # Continue cards read the shared image cache when repainting.
            self._continue_icon_fetching.discard(orig)
            btn = self._continue_icon_pending.pop(orig)
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
                    self._continue_frame.set_entries(self._continue_frame.entries)
            reply.deleteLater()
            return
        elif orig in self._match_icon_pending:
            # "Did you mean?" candidate button.
            self._match_icon_fetching.discard(orig)
            btn = self._match_icon_pending.pop(orig)
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
                    btn.setIcon(QIcon(str(cached)))
                    btn.setIconSize(QSize(30, 42))
            reply.deleteLater()
            return
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
                    self._history_modern.list.viewport().update()
                    self._continue_frame.set_entries(self._continue_frame.entries)
            reply.deleteLater()
            return

        if orig != self._cover_url:
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
        self._ambient_src_version += 1
        size = self.cover_label.size()
        self.cover_label.setPixmap(pixmap)
        self._maybe_auto_accent()
        self._update_ambient()
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
        """Size the cover so the stacked buttons beside it never collide.

        The buttons sit in a ~132px column to the right, so the cover can grow
        much wider than before (capped by the pane width minus that column).
        """
        pane = self._left_pane
        if pane is None:
            return
        avail = pane.width()
        if avail <= 0:
            return
        base = 210 if self.cfg.data.compact else 260
        reserve = 165 if self.cfg.data.compact else 165
        width = min(base, max(70, avail - reserve))
        if self.cfg.data.ui_layout == "modern":
            width = max(90, min(190 if not self.cfg.data.compact else 150, avail // 3))
        height = round(width * 7 / 5)
        if self.cover_label.width() == width:
            return
        self.cover_label.setFixedSize(width, height)
        self._place_cover_spinner()
        if self._cover_pixmap:
            self.cover_label.setPixmap(self._cover_pixmap)

    def resizeEvent(self, event):  # noqa: N802 (Qt naming)
        self._tab_anim_stop()
        self._update_header_for_width()
        self._update_cover_size()
        QTimer.singleShot(0, self._update_cover_size)
        self._update_ambient()
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

    def _open_anilist(self):
        if self._current_payload and self._current_payload.get("anilist_url"):
            webbrowser.open(self._current_payload["anilist_url"])

    def _on_match_pending(self, payload):
        """Populate the inline "Did you mean?" chooser for a shaky match."""
        self._match_panel_url = payload.get("url") or ""
        self._clear_match_chooser()
        candidates = payload.get("candidates") or []
        current_title = payload.get("current_title") or ""
        conf = payload.get("confidence") or 0
        self.match_chooser_label.setText(
            f"Did you mean? · AniList match was only {conf:.0f}% confident"
        )
        for cand in candidates:
            btn = QToolButton()
            btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            btn.setText(cand.get("title") or "?")
            btn.setToolTip(
                (cand.get("title") or "") + ("  ·  current pick" if cand.get("is_current") else "")
            )
            btn.setIconSize(QSize(30, 42))
            if cand.get("is_current"):
                btn.setProperty("current", True)
                btn.setStyleSheet("QToolButton { border: 1px solid palette(highlight); }")
            url = cand.get("url") or ""
            title = cand.get("title") or ""
            btn.clicked.connect(
                lambda _=False, u=url, t=title: self._on_match_choice(u, t)
            )
            self.match_chooser_row.addWidget(btn)
            cover = cand.get("cover") or ""
            if cover:
                cached = self._cover_cache_path(cover)
                if cached.exists():
                    btn.setIcon(QIcon(str(cached)))
                else:
                    self._match_icon_pending[cover] = btn
                    if cover not in self._match_icon_fetching:
                        self._match_icon_fetching.add(cover)
                        self._nam.get(QNetworkRequest(QUrl(cover)))
        if current_title and not any(c.get("is_current") for c in candidates):
            use_btn = QPushButton(f"Use '{current_title}'")
            use_btn.setObjectName("ghost")
            use_btn.setToolTip("Lock in the auto-detected match for this page.")
            use_btn.clicked.connect(
                lambda _=False, u=self._match_panel_url, t=current_title: self._on_match_choice(u, t)
            )
            self.match_chooser_actions.addWidget(use_btn)
        dismiss_btn = QPushButton("Not these")
        dismiss_btn.setObjectName("ghost")
        dismiss_btn.setToolTip("Hide this suggestion; it will not come back for a minute.")
        dismiss_btn.clicked.connect(self._hide_match_chooser)
        self.match_chooser_actions.addWidget(dismiss_btn)
        self.match_chooser.show()

    def _clear_match_chooser(self):
        while self.match_chooser_row.count():
            item = self.match_chooser_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        while self.match_chooser_actions.count():
            item = self.match_chooser_actions.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _hide_match_chooser(self):
        self._match_panel_url = None
        self._clear_match_chooser()
        self.match_chooser.hide()

    def _on_match_choice(self, url, title):
        """Pin the user's pick to this page so it never needs fixing again."""
        self.controller.set_manual_override(url, title, None)
        self._hide_match_chooser()
        self._log_line(f"Match fixed for {url}: {title}")
        self._notify("AniPresence", f"Using '{title}' for this page.")

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

    def _on_share_now(self):
        """Open the local Now Playing page in the browser."""
        if not self.controller.extension.running:
            self._notify("Share", "Enable the browser-extension bridge in Settings first.")
            return
        url = f"http://127.0.0.1:{self.controller.extension.bound_port}/now"
        webbrowser.open(url)
        self._log_line(f"Opening Now Playing page: {url}")

    def _on_share_tunnel(self):
        """Start/stop a cloudflared quick tunnel exposing the Now Playing page."""
        if self._tunnel_proc is not None and self._tunnel_proc.poll() is None:
            self._stop_tunnel()
            return
        if not self.controller.extension.running:
            self._notify("Share", "Enable the browser-extension bridge in Settings first.")
            return
        import re
        import shutil
        import subprocess
        import threading
        import time
        import urllib.request

        exe = shutil.which("cloudflared")
        if not exe:
            self._notify(
                "Share",
                "cloudflared wasn't found. Install it (`winget install cloudflared`), "
                "then try again.",
            )
            return
        port = self.controller.extension.bound_port
        proc = subprocess.Popen(
            [exe, "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        self._tunnel_proc = proc
        self.share_tunnel_btn.setText("Starting tunnel…")
        self.share_tunnel_btn.setEnabled(False)

        def _wait_reachable(public, timeout=75.0):
            """Poll the public URL until Cloudflare routes it to us.

            A brand-new quick tunnel's hostname can take ~30s to resolve (and
            the local resolver often caches the lookup miss), so opening the
            link the instant we copy it fails. Wait until /now.json answers.
            """
            probe = public + "/now.json"
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                try:
                    with urllib.request.urlopen(probe, timeout=10) as resp:
                        if resp.status == 200:
                            return True
                except Exception:  # noqa: BLE001 - DNS/route not ready yet
                    pass
                time.sleep(3)
            return False

        def _watch():
            while proc.poll() is None:
                raw = proc.stderr.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", "replace")
                m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
                if m:
                    public = m.group(0)
                    if not _wait_reachable(public):
                        self._log_line(
                            "Tunnel link handed out before it became reachable; "
                            "DNS may need another moment"
                        )
                    # Link to the page itself, not the bare hostname (which 404s).
                    self.bridge.share_tunnel.emit(public + "/now")
                    return
            self.bridge.share_tunnel.emit("")  # died before handing out a URL

        threading.Thread(target=_watch, daemon=True).start()

    def _stop_tunnel(self):
        proc = self._tunnel_proc
        self._tunnel_proc = None
        self.share_tunnel_btn.setText("Share via tunnel")
        self.share_tunnel_btn.setEnabled(True)
        if proc is not None:
            try:
                proc.terminate()
            except Exception:  # noqa: BLE001 - process already gone
                pass

    def _on_tunnel_public_url(self, public):
        if not public:
            self._stop_tunnel()
            self._notify("Share", "The tunnel failed to start. Is cloudflared installed?")
            return
        QApplication.clipboard().setText(public)
        self.share_tunnel_btn.setText("Stop tunnel")
        self.share_tunnel_btn.setEnabled(True)
        self._log_line(f"Tunnel live: {public} (copied to clipboard)")
        self._notify("Share", f"Public link copied:\n{public}")

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
            dark = 1 if self._applied_theme != "light" else 0
            attr = ctypes.windll.dwmapi.DwmSetWindowAttribute
            attr.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
            dark_var = ctypes.c_int(dark)
            attr(handle, 20, ctypes.byref(dark_var), ctypes.sizeof(dark_var))  # immersive dark mode
            corner = ctypes.c_int(2)  # DWMWA_WINDOW_CORNER_PREFERENCE = 33, value 2 = ROUND
            attr(handle, 33, ctypes.byref(corner), ctypes.sizeof(corner))
            if self._applied_theme == "oled":
                backdrop = ctypes.c_int(1)
                attr(handle, 38, ctypes.byref(backdrop), ctypes.sizeof(backdrop))
            elif self.cfg.data.mica_backdrop or self.cfg.data.cinematic_backdrop:
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
        if not getattr(self, "_native_styles_applied", False):
            self._native_styles_applied = True
            self._apply_native_window_styles()
        if self._start_maximized:
            self._start_maximized = False
            try:
                _declare_user32().ShowWindow(int(self.winId()), SW_MAXIMIZE)
            except Exception:  # noqa: BLE001
                self.showMaximized()
        self.setWindowOpacity(1.0)
        self._apply_backdrop()
        super().showEvent(event)

    def show_placeholder(self):
        self._progress_animation.stop()
        self._current_payload = None
        if hasattr(self,"_cinema_overview") and self.cfg.data.ui_layout == "cinema":
            self._cinema_overview.refresh()
        self._load_banner("")
        self.anime_title.setText("No anime detected")
        self._refresh_overview_logo()
        self.episode_label.setText("WATCHING A VIDEO IS ALL IT TAKES")
        self.match_label.setText("")
        self.progress.setValue(0)
        self.time_cur.setText("00:00")
        self.time_total.setText("00:00")
        self.cover_label.clear()
        self.cover_label.setText("Cover")
        self._cover_pixmap = None
        self._ambient_src_version += 1
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
            self._reset_tray_icon()
        self._update_ambient()
        self._update_preview()

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
        if not self._quitting and not self._closing_fade:
            # Close button pressed: fade the window out, then really close.
            event.ignore()
            self._closing_fade = True
            self._fade_out_on_close()
            return
        self._save_window_state()
        self._unregister_hotkey()
        self._stop_tunnel()
        if self._login_flow:
            self._login_flow.cancel()
            self._login_generation += 1
        self.controller.request_stop()
        self._artwork_timer.stop()
        self._artwork.close()
        super().closeEvent(event)

    def _fade_out_on_close(self):
        """Fade the whole window out smoothly before quitting."""
        if self._fade_anim is not None:
            self._fade_anim.stop()
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(200)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.InOutCubic)
        anim.finished.connect(self.close)
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        self._fade_anim = anim
