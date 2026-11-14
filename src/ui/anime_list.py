"""Consistent anime rows without the platform's redundant focus rectangle."""
import math
from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPalette, QPixmap, QPainterPath, QPen
from PySide6.QtWidgets import QListWidget, QStyle, QStyledItemDelegate, QStyleOptionViewItem


DETAIL_ROLE = Qt.UserRole + 3


class AnimeDelegate(QStyledItemDelegate):
    def modern(self):
        window = self.parent().window()
        return hasattr(window,"cfg") and window.cfg.data.ui_layout == "modern"

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        option.state &= ~QStyle.State_HasFocus

    def sizeHint(self, option, index):
        if index.data(Qt.UserRole + 1):
            return QSize(180, 92 if self.modern() else 72)
        return super().sizeHint(option, index)

    def paint(self, painter, option, index):
        entry = index.data(Qt.UserRole + 1)
        if not isinstance(entry, dict) or not entry.get("title"):
            return super().paint(painter, option, index)
        window = self.parent().window()
        if hasattr(window, "_artwork"):
            entry = window._artwork.enrich(entry, episode=self.parent().property("variant") == "history", priority=5)
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        icon = QIcon(opt.icon)
        opt.text = ""
        opt.icon = QIcon()
        widget = opt.widget
        widget.style().drawControl(QStyle.CE_ItemViewItem, opt, painter, widget)
        if self.modern():
            self.paint_modern(painter, option, opt, index, entry, icon)
            return
        painter.save()
        painter.setRenderHint(painter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(12, 7, -14, -7)
        cover = QRect(rect.x(), rect.y(), 40, 56)
        window = self.parent().window()
        url = entry.get("episode_art") or entry.get("cover") or ""
        dpr = painter.device().devicePixelRatioF()
        pixmap = window._images.get(window._cover_cache_path(url), math.ceil(40*dpr), math.ceil(56*dpr)) if url and hasattr(window, "_images") else QPixmap()
        if not pixmap.isNull():
            from .poster import draw_filled_pixmap
            draw_filled_pixmap(painter, QRectF(cover), pixmap, 5)
        else:
            if url and hasattr(window, "_request_library_poster"):
                window._request_library_poster(url)
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

    def paint_modern(self, painter, option, opt, index, entry, icon):
        painter.save(); painter.setRenderHint(painter.RenderHint.Antialiasing)
        area=QRectF(option.rect.adjusted(2,2,-2,-2))
        light=self.parent().window()._applied_theme=="light"
        accent=QColor(self.parent().window().cfg.data.accent)
        painter.setPen(QPen(QColor(155,155,155,50),1)); painter.setBrush(QColor(145,145,145,10))
        painter.drawRoundedRect(area,10,10)
        rect=option.rect.adjusted(12,12,-14,-12)
        window = self.parent().window()
        url = entry.get("episode_art") or entry.get("cover") or ""
        dpr = painter.device().devicePixelRatioF()
        pm = window._images.get(window._cover_cache_path(url), math.ceil(112*dpr), math.ceil(64*dpr)) if url else QPixmap()
        if url and pm.isNull():
            window._request_library_poster(url)
        landscape = not pm.isNull() and pm.width() > pm.height()
        cover=QRect(rect.x(),rect.y(),112 if landscape and rect.width()>760 else 46,64)
        clip=QPainterPath(); clip.addRoundedRect(QRectF(cover),7,7)
        painter.save(); painter.setClipPath(clip)
        if not pm.isNull():
            from .poster import draw_filled_pixmap
            draw_filled_pixmap(painter,QRectF(cover),pm,7)
        else:painter.fillRect(cover,QColor(145,145,145,35))
        painter.restore()
        left=cover.right()+18; width=max(1,rect.right()-left)
        score=entry.get("mean_score")
        reserve=66 if score else 0
        title_rect=QRect(left,rect.y()+1,max(1,width-reserve),23)
        font=QFont(option.font); font.setPixelSize(15); font.setWeight(QFont.DemiBold)
        painter.setFont(font); painter.setPen(QColor("#211b36" if light else "#f3f3f3"))
        title=(f"{entry['rank']}. " if entry.get("rank") else "")+entry["title"]
        painter.drawText(title_rect,Qt.AlignVCenter,QFontMetrics(font).elidedText(title,Qt.ElideRight,title_rect.width()))
        if score:
            gold = QColor("#b78926" if light else "#f5d475")
            star = QPainterPath()
            for i in range(10):
                angle = -math.pi / 2 + i * math.pi / 5
                radius = 7 if i % 2 == 0 else 3
                x = rect.right() - 48 + math.cos(angle) * radius
                y = rect.y() + 12 + math.sin(angle) * radius
                if i == 0:
                    star.moveTo(x, y)
                else:
                    star.lineTo(x, y)
            star.closeSubpath()
            painter.fillPath(star, gold)
            painter.setPen(gold)
            painter.drawText(QRect(rect.right()-37,rect.y(),37,24),Qt.AlignRight|Qt.AlignVCenter,str(score))
        font.setPixelSize(12); font.setWeight(QFont.Normal); painter.setFont(font)
        painter.setPen(QColor("#716681" if light else "#b1b1b1"))
        detail=index.data(DETAIL_ROLE) or ""
        if self.parent().property("variant") == "history" and entry.get("episode_title"):
            old = (index.data(Qt.UserRole + 1) or {}).get("episode_title")
            if old:
                detail = detail.replace(old, entry["episode_title"])
            else:
                first, separator, rest = detail.partition(" · ")
                detail = first + " · " + entry["episode_title"] + separator + rest
        painter.drawText(QRect(left,rect.y()+26,width,20),Qt.AlignVCenter,QFontMetrics(font).elidedText(detail,Qt.ElideRight,width))
        variant=self.parent().property("variant")
        if variant=="ranking": fraction=entry.get("share",0)
        elif variant=="history": fraction=float(entry.get("current_time") or 0)/float(entry.get("duration") or 1)
        else:fraction=float(entry.get("progress") or 0)/float(entry.get("episodes") or 1)
        if (variant=="ranking" or entry.get("episodes") or entry.get("duration")):
            bar=QRectF(left,rect.bottom()-7,width,5)
            painter.setPen(Qt.NoPen); painter.setBrush(QColor(120,105,160,45)); painter.drawRoundedRect(bar,2.5,2.5)
            painter.setBrush(accent.lighter(130)); bar.setWidth(width*max(0,min(1,fraction))); painter.drawRoundedRect(bar,2.5,2.5)
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
        if not isinstance(entry, dict):
            return None
        return tuple(entry["history_key"]) if entry.get("history_key") else (entry.get("url"), entry.get("title"), entry.get("episode"))

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
