"""Bundled seasonal illustrations, decoded once and cached per scene."""
from pathlib import Path
from PySide6.QtGui import QPixmap
from .poster import draw_filled_pixmap

SCENES = ("Spring Bloom", "Summer Sunset", "Autumn Ember", "Winter Frost",
          "Cherry Blossom", "Ocean Depths", "Forest Dew")
_scenes = {}


def theme_art(name):
    if not _scenes:
        atlas = QPixmap(str(Path(__file__).resolve().parents[2] / "assets" / "theme-atlas.webp"))
        if atlas.isNull():
            return QPixmap()
        for index, scene in enumerate(SCENES):
            # Proportional boundaries also handle odd-sized source atlases.
            x0, x1 = (index % 2)*atlas.width()//2, (index % 2+1)*atlas.width()//2
            y0, y1 = (index//2)*atlas.height()//4, (index//2+1)*atlas.height()//4
            _scenes[scene] = atlas.copy(x0, y0, x1-x0, y1-y0)
    return _scenes.get(name, QPixmap())


def paint_theme(painter, rect, name, light=False):
    art = theme_art(name)
    if not art.isNull():
        draw_filled_pixmap(painter, rect, art, 0)
