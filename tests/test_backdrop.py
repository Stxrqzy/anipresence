"""Cinematic backdrop styles: color sampling and full-window renders."""
import os
import sys

import pytest
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.config import Config
from src.ui.app import (
    MainWindow,
    _ambient_box_colors,
    _ambient_diagonal_colors,
    _ambient_gradient_colors,
)


@pytest.fixture(scope="module", autouse=True)
def app():
    existing = QApplication.instance()
    qapp = existing or QApplication(sys.argv)
    yield qapp


def _fake_window():
    """Uninitialized MainWindow whose methods only read self.cfg (duotone)."""
    win = MainWindow.__new__(MainWindow)
    win.cfg = type("C", (), {"data": Config(accent="#7c5cff")})()
    return win


def _two_tone():
    """Blue top half → red bottom half."""
    pm = QPixmap(160, 200)
    pm.fill(QColor(10, 10, 200))
    p = QPainter(pm)
    p.fillRect(0, 100, 160, 100, QColor(200, 10, 10))
    p.end()
    return pm


def _diagonal_tone():
    """Blue top-left quarter → red bottom-right quarter, green elsewhere."""
    pm = QPixmap(160, 200)
    pm.fill(QColor(40, 200, 40))
    p = QPainter(pm)
    p.fillRect(0, 0, 80, 100, QColor(10, 10, 200))
    p.fillRect(80, 100, 80, 100, QColor(200, 10, 10))
    p.end()
    return pm


def test_gradient_colors_capture_top_and_bottom():
    top, bottom = _ambient_gradient_colors(_two_tone())
    assert top.blue() > top.red()  # top band is blue-dominant
    assert bottom.red() > bottom.blue()  # bottom band is red-dominant


def test_diagonal_colors_capture_corners():
    top_left, bottom_right = _ambient_diagonal_colors(_diagonal_tone())
    assert top_left.blue() > top_left.red()
    assert bottom_right.red() > bottom_right.blue()


def test_box_colors_fallback_on_unusable_source():
    default = QColor(30, 32, 42)
    colors = _ambient_box_colors(QPixmap(), [((0, 0, 1, 1), default)])
    assert colors[0] == default  # null pixmap → default, no crash


def _render(fake, source, tw, th, style="frosted"):
    return MainWindow._render_ambient(fake, source, tw, th, style)


@pytest.mark.parametrize(
    "style",
    ["frosted", "dreamy", "gradient", "diagonal", "vignette", "duotone"],
)
def test_every_style_fills_the_window(style):
    result = _render(_fake_window(), _two_tone(), 960, 780, style)
    assert not result.isNull()
    assert (result.width(), result.height()) == (960, 780)
    top = QColor(result.toImage().pixel(480, 4))
    assert top.alpha() == 255  # opaque, not the letterbox black fill
    assert top.name() != "#000000"


def test_dreamy_blur_is_lighter_than_frosted():
    fake = _fake_window()
    frosted = _render(fake, _two_tone(), 960, 780, "frosted")
    dreamy = _render(fake, _two_tone(), 960, 780, "dreamy")
    f = QColor(frosted.toImage().pixel(480, 380))
    d = QColor(dreamy.toImage().pixel(480, 380))
    assert d.lightness() > f.lightness()  # heavier blur → milkier wash


def test_default_style_is_frosted():
    assert _render(_fake_window(), _two_tone(), 960, 780) is not None