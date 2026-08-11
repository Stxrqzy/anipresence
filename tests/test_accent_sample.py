"""Cover-art accent sampling used by the auto-accent feature."""
import os
import sys

import pytest
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.ui.app import _sample_accent_color


@pytest.fixture(scope="module")
def app():
    existing = QApplication.instance()
    if existing is not None:
        return existing
    return QApplication(sys.argv)


def _solid(color):
    pm = QPixmap(128, 128)
    pm.fill(QColor(color))
    return pm


def test_null_pixmap_returns_empty(app):
    assert _sample_accent_color(None) == ""
    assert _sample_accent_color(QPixmap()) == ""


def test_solid_color_roundtrips(app):
    # A single vivid color should come back close to itself.
    got = _sample_accent_color(_solid("#20c040"))
    assert got and QColor(got).isValid()
    assert abs(QColor(got).hue() - QColor("#20c040").hue()) <= 2


def test_needs_saturation(app):
    # Near-gray/white art has no usable accent -> empty.
    assert _sample_accent_color(_solid("#f8f8f8")) == ""
    assert _sample_accent_color(_solid("#888888")) == ""


def test_returns_vivid_color(app):
    got = _sample_accent_color(_solid("#1234ff"))
    assert got
    color = QColor(got)
    assert color.saturation() >= 160  # pushed vivid
    assert 120 <= color.value() <= 200  # kept in a readable range
