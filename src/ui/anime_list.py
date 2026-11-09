"""Consistent anime rows without the platform's redundant focus rectangle."""
from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPalette
from PySide6.QtWidgets import QListWidget, QStyle, QStyledItemDelegate, QStyleOptionViewItem


DETAIL_ROLE = Qt.UserRole + 3


class AnimeDelegate(QStyledItemDelegate):
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        option.state &= ~QStyle.State_HasFocus

    def sizeHint(self, option, index):
        if index.data(Qt.UserRole + 1):
            return QSize(180, 72)
        return super().sizeHint(option, index)

    def paint(self, painter, option, index):
        entry = index.data(Qt.UserRole + 1)
        if not isinstance(entry, dict) or not entry.get("title"):
            return super().paint(painter, option, index)
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        icon = QIcon(opt.icon)
        opt.text = ""
        opt.icon = QIcon()
        widget = opt.widget
        widget.style().drawControl(QStyle.CE_ItemViewItem, opt, painter, widget)
        painter.save()
        painter.setRenderHint(painter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(12, 7, -14, -7)
        cover = QRect(rect.x(), rect.y(), 40, 56)
        if not icon.isNull():
            icon.paint(painter, cover, Qt.AlignCenter, QIcon.Normal)
        else:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(120, 140, 180, 24))
            painter.drawRoundedRect(cover, 5, 5)
        text_rect = QRect(cover.right() + 14, rect.y() + 5,
                          max(0, rect.right() - cover.right() - 14), 23)
        font = QFont(option.font)
        font.setWeight(QFont.DemiBold)
        painter.setFont(font)
        text_color = opt.palette.color(QPalette.Text)
        painter.setPen(text_color)
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft,
                         QFontMetrics(font).elidedText(entry["title"], Qt.ElideRight, text_rect.width()))
        font.setWeight(QFont.Normal)
        painter.setFont(font)
        text_color.setAlpha(170)
        painter.setPen(text_color)
        detail = index.data(DETAIL_ROLE) or ""
        text_rect.translate(0, 24)
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft,
                         QFontMetrics(font).elidedText(detail, Qt.ElideRight, text_rect.width()))
        painter.restore()


class AnimeList(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("animeList")
        self.setItemDelegate(AnimeDelegate(self))
        self.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSpacing(3)
        self.setIconSize(QSize(40, 56))
        self.setUniformItemSizes(False)

    @staticmethod
    def _identity(item):
        entry = item.data(Qt.UserRole + 1) if item else None
        return (entry.get("url"), entry.get("title")) if isinstance(entry, dict) else None

    def view_state(self):
        return self._identity(self.currentItem()), self.verticalScrollBar().value()

    def restore_view(self, state):
        identity, position = state
        if identity:
            for i in range(self.count()):
                if self._identity(self.item(i)) == identity:
                    self.setCurrentItem(self.item(i))
                    break
        self.verticalScrollBar().setValue(position)
