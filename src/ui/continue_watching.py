"""Artwork-led, keyboard-accessible continue-watching cards."""
import math
from PySide6.QtCore import Qt, QPointF, QRectF, QSize, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QRadialGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QAbstractButton, QGridLayout, QHBoxLayout, QLabel, QPushButton, QToolButton, QVBoxLayout, QWidget


class ContinueCard(QAbstractButton):
    removed = Signal(object)

    def __init__(self, entry, cover_path, parent=None):
        super().__init__(parent)
        self.entry = entry
        self.cover_path = cover_path
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumWidth(200)
        self.setAccessibleName(f"Resume {entry.get('title')} episode {entry.get('episode') or ''}")
        self.setToolTip(self.accessibleName() + (" · " + entry["episode_title"] if entry.get("episode_title") else ""))
        self.close_button = QToolButton(self)
        self.close_button.setText("×")
        self.close_button.setAccessibleName("Remove from continue watching")
        self.close_button.setToolTip("Remove from continue watching")
        self.close_button.setFixedSize(24, 24)
        self.close_button.setStyleSheet("QToolButton { background: rgba(16,12,26,180); color: white; border: none; border-radius: 12px; font-size: 18px; }")
        self.close_button.clicked.connect(lambda: self.removed.emit(self.entry))

    def sizeHint(self):
        return QSize(270, 330)

    def resizeEvent(self, event):
        self.close_button.move(self.width()-32, 8)
        super().resizeEvent(event)

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        window = self.window()
        light = window._applied_theme == "light"
        accent = QColor(window.cfg.data.accent)
        ink = QColor("#241b37" if light else "#f6f2ff")
        muted = QColor("#746783" if light else "#b9aecb")
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect().adjusted(1, 1, -1, -1))
        clip = QPainterPath(); clip.addRoundedRect(rect, 18, 18)
        p.setClipPath(clip)
        p.fillRect(rect, QColor("#f4effa" if light else "#12101f"))
        art_h = self.height()-166
        area = QRectF(1, 1, self.width()-2, art_h)
        pixmap = QPixmap()
        for field in ("episode_art", "backdrop", "banner_url", "cover"):
            if self.entry.get(field):
                pixmap = QPixmap(str(self.cover_path(self.entry[field])))
                if not pixmap.isNull():
                    break
        if not pixmap.isNull():
            scaled = pixmap.scaled(int(area.width()), int(area.height()), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            p.save(); p.setClipRect(area, Qt.IntersectClip)
            p.drawPixmap(int((self.width()-scaled.width())/2), int((art_h-scaled.height())/2), scaled)
            p.restore()
        else:
            gradient = QLinearGradient(0, 0, self.width(), art_h)
            gradient.setColorAt(0, accent.darker(250)); gradient.setColorAt(1, accent.darker(450))
            p.fillRect(area, gradient)
            p.setPen(accent.lighter(150)); font = QFont(self.font()); font.setPixelSize(48); p.setFont(font)
            p.drawText(area, Qt.AlignCenter, "▷")
        fade = QLinearGradient(0, art_h-42, 0, art_h+8)
        base = QColor("#f4effa" if light else "#12101f")
        fade.setColorAt(0, QColor(base.red(), base.green(), base.blue(), 0)); fade.setColorAt(1, base)
        p.fillRect(QRectF(0, art_h-42, self.width(), 50), fade)
        glow = QRadialGradient(self.width()-20, self.height()+20, self.width()*.7)
        glow.setColorAt(0, QColor(accent.red(), accent.green(), accent.blue(), 60 if self.underMouse() else 26))
        glow.setColorAt(1, QColor(0, 0, 0, 0)); p.fillRect(rect, glow)
        x, width = 16, self.width()-32
        font = QFont(self.font()); font.setPixelSize(16); font.setWeight(QFont.Bold); p.setFont(font); p.setPen(ink)
        p.drawText(QRectF(x, art_h+8, width, 24), Qt.AlignVCenter,
                   p.fontMetrics().elidedText(self.entry.get("title") or "Unknown", Qt.ElideRight, width))
        font.setPixelSize(12); font.setWeight(QFont.Normal); p.setFont(font); p.setPen(muted)
        episode = f"Ep {self.entry.get('episode') or '—'}"
        if self.entry.get("episode_title"):
            episode += " · " + self.entry["episode_title"]
        p.drawText(QRectF(x, art_h+38, width, 38), Qt.TextWordWrap, p.fontMetrics().elidedText(episode, Qt.ElideRight, width*2-14))
        duration = max(0, float(self.entry.get("duration") or 0))
        current = max(0, float(self.entry.get("current_time") or 0))
        fraction = min(1, current/duration) if duration else 0
        bar = QRectF(x, art_h+94, width-46, 7)
        p.setPen(Qt.NoPen); p.setBrush(QColor(135, 120, 165, 55)); p.drawRoundedRect(bar, 3.5, 3.5)
        if duration:
            bar.setWidth(bar.width()*fraction)
            fill = QLinearGradient(bar.topLeft(), bar.topRight()); fill.setColorAt(0, accent); fill.setColorAt(1, accent.lighter(145))
            p.setBrush(fill); p.drawRoundedRect(bar, 3.5, 3.5)
        p.setPen(ink); p.drawText(QRectF(self.width()-57, art_h+85, 40, 22), Qt.AlignRight|Qt.AlignVCenter,
                                 f"{fraction:.0%}" if duration else "—")
        p.setPen(muted)
        remaining = f"{math.ceil(max(0, duration-current)/60)}m left" if duration else "Not recorded"
        p.setBrush(Qt.NoBrush); p.setPen(QPen(muted, 1.2))
        clock = QPointF(x+8, art_h+134); p.drawEllipse(clock, 7, 7)
        p.drawLine(clock, QPointF(clock.x(), clock.y()-4))
        p.drawLine(clock, QPointF(clock.x()+3, clock.y()+2))
        p.drawText(QRectF(x+23, art_h+122, width-70, 24), Qt.AlignVCenter, remaining)
        circle = QRectF(self.width()-54, self.height()-50, 36, 36)
        p.setPen(QPen(accent.lighter(140), 1.5)); p.setBrush(accent.darker(240)); p.drawEllipse(circle)
        triangle = QPainterPath(QPointF(circle.center().x()-4, circle.center().y()-7))
        triangle.lineTo(circle.center().x()+7, circle.center().y())
        triangle.lineTo(circle.center().x()-4, circle.center().y()+7); triangle.closeSubpath()
        p.fillPath(triangle, QColor("white"))
        p.setClipping(False); p.setBrush(Qt.NoBrush)
        border = accent.lighter(140) if self.underMouse() or self.hasFocus() else QColor(accent.red(), accent.green(), accent.blue(), 110)
        p.setPen(QPen(border, 2 if self.hasFocus() else 1)); p.drawRoundedRect(rect, 18, 18)


class ContinueWatching(QWidget):
    activated = Signal(object)
    removed = Signal(object)
    view_all = Signal()

    def __init__(self, cover_path):
        super().__init__()
        self.cover_path = cover_path
        self.cards = []
        self.entries = []
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 12, 0, 12); layout.setSpacing(18)
        header = QHBoxLayout(); header.setSpacing(16); text = QVBoxLayout()
        self.symbol = QLabel(); self.symbol.setFixedSize(54,54); header.addWidget(self.symbol)
        self.heading = QLabel("Continue Watching"); self.heading.setObjectName("cardHeading")
        self.heading.setStyleSheet("font-size: 24px; font-weight: 700;")
        text.addWidget(self.heading)
        subtitle = QLabel("Pick up where you left off."); subtitle.setObjectName("subLabel"); text.addWidget(subtitle)
        header.addLayout(text, 1)
        all_button = QPushButton("View all  →"); all_button.setObjectName("ghost"); all_button.clicked.connect(self.view_all.emit)
        header.addWidget(all_button); layout.addLayout(header)
        self.grid = QGridLayout(); self.grid.setSpacing(14); layout.addLayout(self.grid)

    def configure(self, accent):
        self.heading.setText(f'Continue <span style="color:{QColor(accent).name()}">Watching</span>')
        pixmap = QPixmap(54,54); pixmap.fill(Qt.transparent)
        p = QPainter(pixmap); p.setRenderHint(QPainter.Antialiasing)
        color = QColor(accent); p.setPen(QPen(color.lighter(140), 1.5)); p.setBrush(color.darker(310))
        p.drawRoundedRect(QRectF(1,1,52,52),16,16)
        triangle = QPainterPath(QPointF(21,17)); triangle.lineTo(37,27); triangle.lineTo(21,37); triangle.closeSubpath()
        p.setPen(QPen(color.lighter(180), 2.5)); p.setBrush(Qt.NoBrush); p.drawPath(triangle); p.end()
        self.symbol.setPixmap(pixmap)
        for card in self.cards: card.update()

    def set_entries(self, entries):
        entries = entries[:4]
        if entries == self.entries:
            for card in self.cards: card.update()
            return
        if len(entries) == len(self.entries) and all(
                (a.get("media_id") or a.get("anilist_id"), a.get("title")) ==
                (b.get("media_id") or b.get("anilist_id"), b.get("title")) for a, b in zip(entries, self.entries)):
            self.entries = entries
            for card, entry in zip(self.cards, entries):
                card.entry = entry
                card.setAccessibleName(f"Resume {entry.get('title')} episode {entry.get('episode') or ''}")
                card.setToolTip(card.accessibleName() + (" · " + entry["episode_title"] if entry.get("episode_title") else ""))
                card.update()
            return
        self.entries = entries
        for card in self.cards:
            self.grid.removeWidget(card); card.deleteLater()
        self.cards = []
        for entry in entries:
            card = ContinueCard(entry, self.cover_path, self)
            card.clicked.connect(lambda checked=False, c=card: self.activated.emit(c.entry))
            card.removed.connect(self.removed.emit)
            self.cards.append(card)
        self._reflow()
        self.setVisible(bool(entries))

    def _reflow(self):
        columns = max(1, min(4, (self.width()+14)//234))
        width = max(200, (self.width()-(columns-1)*14)//columns)
        for card in self.cards: self.grid.removeWidget(card)
        for i, card in enumerate(self.cards):
            card.setFixedHeight(int(width*.57)+166)
            self.grid.addWidget(card, i//columns, i%columns)
        for i in range(4): self.grid.setColumnStretch(i, 1 if i < columns else 0)

    def resizeEvent(self, event):
        self._reflow()
        super().resizeEvent(event)
