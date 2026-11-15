"""V3: full-width, artwork-first pages with compact, neutral chrome."""
import math
from datetime import datetime
from PySide6.QtCore import Qt, QRectF, QSize, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QLinearGradient, QPen
from PySide6.QtWidgets import (QAbstractButton, QFrame, QGridLayout, QHBoxLayout, QLabel,
                              QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget, QComboBox)
from .details import Artwork, Hero, text, ImageReveal, artwork_placeholder, watched_badge
from .poster import draw_filled_pixmap


def action(caption, slot, name="cinemaPill"):
    button = QPushButton(caption); button.setObjectName(name)
    button.clicked.connect(slot)
    return button


def clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().hide()
            item.widget().deleteLater()
        elif item.layout():
            clear(item.layout())


class Page(QScrollArea):
    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True); self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        host = QWidget(); host.setObjectName("surfaceContent")
        self.body = QVBoxLayout(host); self.body.setContentsMargins(24, 16, 24, 24); self.body.setSpacing(24)
        self.setWidget(host)


class LandscapeCard(QAbstractButton):
    removed = Signal(object)

    def __init__(self, entry, history=False):
        super().__init__()
        self.entry = dict(entry); self.history = history
        self.reveal=ImageReveal(self)
        self.setMinimumWidth(160); self.setFixedHeight(210)
        self.setCursor(Qt.PointingHandCursor); self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName(f"{entry.get('title')} · Episode {entry.get('episode') or '—'}")
        self.close = action("×", lambda: self.removed.emit(self.entry))
        self.close.setParent(self); self.close.setFixedSize(32, 32)
        self.close.setAccessibleName("Remove history entry" if history else "Remove from Continue Watching")
        self.close.setStyleSheet("QPushButton {background:rgba(0,0,0,175);color:white;border:0;border-radius:16px;font-size:22px;padding:0;min-height:0;}")
        self.close.show()
        self.clicked.connect(lambda: self.window()._open_anime(self.entry))

    def resizeEvent(self, event):
        self.close.move(self.width()-44, 12)
        super().resizeEvent(event)

    def paintEvent(self, event):
        w = self.window()
        e = w._artwork.enrich(self.entry, episode=True, priority=2)
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect().adjusted(1, 1, -1, -1))
        clip = QPainterPath(); clip.addRoundedRect(rect, 22, 22); p.setClipPath(clip)
        p.fillRect(rect, QColor("#191919"))
        artwork_placeholder(p,rect)
        for key in ("episode_art", "backdrop", "banner_url", "cover"):
            url = e.get(key)
            if not url:
                continue
            pm = w._images.get(w._cover_cache_path(url), 800, 450)
            if pm.isNull():
                w._request_library_poster(url)
            else:
                p.setOpacity(self.reveal.opacity((url,pm.cacheKey())))
                draw_filled_pixmap(p, rect, pm, 0);p.setOpacity(1);break
        shade = QLinearGradient(0, self.height()*.2, 0, self.height())
        shade.setColorAt(0, QColor(0, 0, 0, 0)); shade.setColorAt(1, QColor(0, 0, 0, 235))
        p.fillRect(rect, shade)
        font = QFont(self.font()); font.setPixelSize(20); font.setBold(True); p.setFont(font); p.setPen(Qt.white)
        p.drawText(QRectF(18, self.height()-67, self.width()-36, 30), Qt.AlignVCenter,
                   p.fontMetrics().elidedText(e.get("title") or "Unknown", Qt.ElideRight, self.width()-36))
        font.setPixelSize(13); font.setBold(False); p.setFont(font); p.setPen(QColor("#cccccc"))
        detail = f"Episode {e.get('episode') or '—'}"
        duration, current = e.get("duration") or 0, e.get("current_time") or 0
        if self.history:
            detail += " · " + (e.get("episode_title") or e.get("history_source") or "Local playback")
            if duration and current/duration>=.9:detail += " · Watched"
        elif e.get("resume_kind")=="caught_up":
            detail="All caught up · View details"
        elif e.get("resume_kind")=="next":
            detail=f"Up next · Episode {e.get('episode')}"
        elif duration:
            detail += f" · {min(100,round(current/duration*100))}% · {math.ceil(max(0, duration-current)/60)}m left"
        elif e.get("progress"):
            detail += f" · {e['progress']} watched"
        p.drawText(QRectF(18, self.height()-35, self.width()-36, 22), Qt.AlignVCenter,
                   p.fontMetrics().elidedText(detail, Qt.ElideRight, self.width()-36))
        if duration:
            p.fillRect(QRectF(0, self.height()-5, self.width(), 4), QColor(255,255,255,50))
            p.fillRect(QRectF(0, self.height()-5, self.width()*min(1,current/duration), 4), Qt.white)
        if self.history and ((duration and current/duration>=.9) or
                             (not duration and e.get("history_source")=="AniList feed" and e.get("episode"))):
            watched_badge(p,QRectF(12,12,32,32))
        if self.hasFocus() or self.underMouse():
            p.setClipping(False); p.setBrush(Qt.NoBrush); p.setPen(QPen(Qt.white, 2)); p.drawRoundedRect(rect,22,22)


class Overview(Page):
    def __init__(self):
        super().__init__()
        self.hero = Hero(); self.hero.setMinimumHeight(350)
        body = QVBoxLayout(self.hero); body.setContentsMargins(36, 36, 36, 36); body.setSpacing(16)
        self.eyebrow = text("YOUR ANIME, IN THE MOMENT", "cinemaMuted")
        self.logo = Artwork(fit=True); self.logo.setFixedHeight(115); self.logo.setMaximumWidth(440)
        self.title = text("Make time for a good story.", "cinemaHeroTitle")
        self.subtitle = text("Start watching in your browser. Your anime and progress appear here.", "detailSynopsis")
        self.button = action("Discover anime", self.activate, "cinemaPrimary")
        self.button.setMaximumWidth(290)
        for widget in (self.eyebrow, self.logo, self.title, self.subtitle, self.button): body.addWidget(widget)
        body.addStretch(); self.body.addWidget(self.hero)
        row = QHBoxLayout(); row.addWidget(text("Continue Watching", "cinemaHeading"), 1)
        row.addWidget(action("View all ›", lambda: self.window()._navigate(self.window()._history_tab_index), "ghost"))
        self.body.addLayout(row)
        self.grid = QGridLayout(); self.grid.setSpacing(14); self.body.addLayout(self.grid)
        self.empty = text("Your next episode will be waiting here.", "cinemaMuted"); self.body.addWidget(self.empty)
        self.body.addWidget(text("Your space", "cinemaHeading"))
        links = QHBoxLayout()
        for caption, index in (("Browse anime", 1), ("Watchlist", 3), ("Watch time", 4)):
            links.addWidget(action(caption, lambda checked=False, i=index: self.window()._navigate(i)))
        self.body.addLayout(links); self.body.addStretch()
        self.cards = []
        self.payload = {}

    def activate(self):
        if self.payload:
            self.window()._open_anime(self.payload)
        else:
            self.window()._navigate(self.window()._browse_tab_index)

    def refresh(self, refresh_cards=True):
        w = self.window(); e = w._current_payload or {}
        self.payload = e
        self.hero.url = e.get("backdrop") or e.get("banner_url") or e.get("cover_url") or e.get("cover") or ""
        self.logo.url = e.get("title_logo") or ""
        show_logo = bool(w.cfg.data.overview_title_logo and self.logo.url) and not self.logo.pixmap().isNull()
        self.logo.setVisible(show_logo); self.title.setVisible(not show_logo)
        self.title.setText(e.get("title") or "Make time for a good story.")
        self.eyebrow.setText("PAUSED" if e.get("paused") else "NOW WATCHING" if e else "YOUR ANIME, IN THE MOMENT")
        self.subtitle.setText(f"Episode {e.get('episode') or '—'} · {e.get('website') or 'Playing in your browser'}" if e else
                              "Start watching in your browser. Your anime and progress appear here.")
        self.button.setText("Continue watching" if e else "Discover anime")
        state=(id(w.controller.watchlog),w.controller.watchlog.revision,w._continue_frame.entries)
        if not refresh_cards or state==getattr(self,"_continue_state",None):
            self.hero.update();self.logo.update()
            for card in self.cards:card.update()
            return
        self._continue_state=state
        from .playback import playback_state, resume_entry
        history=w.controller.watchlog.history(w.controller.recent.items(),copy_result=False)
        library=w._library_modern.entries
        entries=[resume_entry(e,playback_state(e,history,library)) for e in w._continue_frame.entries]
        if entries != [c.entry for c in self.cards]:
            clear(self.grid); self.cards = []
            for entry in entries:
                card = LandscapeCard(entry); card.removed.connect(w._remove_continue); self.cards.append(card)
            self.reflow()
        self.empty.setVisible(not entries)
        self.hero.update(); self.logo.update()
        for card in self.cards: card.update()

    def reflow(self):
        columns = max(1, min(4, self.viewport().width()//290))
        for i, card in enumerate(self.cards):
            self.grid.addWidget(card, i//columns, i%columns)
            card.setFixedHeight(200)
        for i in range(4):self.grid.setColumnStretch(i,1 if i<columns else 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reflow()


class History(Page):
    def __init__(self):
        super().__init__()
        self.entries = []; self.limit = 48; self.cards = []; self.groups = []
        self.body.addWidget(text("Watch history", "cinemaHeading"))
        toolbar = QHBoxLayout()
        self.search = QLineEdit(); self.search.setPlaceholderText("Search watch history…")
        self.order = QComboBox(); self.order.addItems(["Newest first", "Oldest first"])
        toolbar.addWidget(self.search,1); toolbar.addWidget(self.order)
        toolbar.addWidget(action("Clear history", lambda: self.window()._on_clear_history()))
        self.body.addLayout(toolbar)
        self.note = text("Local playback + AniList feed · Feed dates are list-update dates.", "cinemaMuted")
        self.body.addWidget(self.note)
        self.content = QVBoxLayout(); self.content.setSpacing(22); self.body.addLayout(self.content)
        self.more = action("Load more", self.load_more); self.body.addWidget(self.more); self.body.addStretch()
        self.search.textChanged.connect(self.reset); self.order.currentIndexChanged.connect(self.reset)

    def reset(self):
        self.limit = 48; self.refresh()

    def load_more(self):
        self.limit += 48; self.refresh()

    def refresh(self):
        from ..core.history import sorted_history
        w = self.window()
        entries = w.controller.watchlog.history(w.controller.recent.items(), copy_result=False)
        query = self.search.text().casefold()
        rows = sorted_history([e for e in entries if query in (e.get("title","")+" "+e.get("episode_title","")).casefold()],
                              newest_first=self.order.currentIndex()==0)
        clear(self.content); self.cards = []; self.groups = []
        day = None; grid = None
        for e in rows[:self.limit]:
            stamp = e.get("last_watched") or 0
            key = datetime.fromtimestamp(stamp).strftime("%B %d, %Y") if stamp else "Earlier history"
            if key != day:
                self.content.addWidget(text(key, "cinemaSection"))
                grid = QGridLayout(); grid.setSpacing(14); self.content.addLayout(grid)
                self.groups.append((grid, [])); day = key
            card = LandscapeCard(e, history=True)
            card.removed.connect(self.remove)
            self.groups[-1][1].append(card); self.cards.append(card)
        if not rows: self.content.addWidget(text("No matching episodes. Play an anime to start your history.", "cinemaMuted"))
        self.more.setVisible(len(rows)>self.limit)
        self.reflow()

    def remove(self, entry):
        self.window().controller.remove_recent_entry(entry)
        self.refresh()

    def reflow(self):
        columns = max(1, min(4, self.viewport().width()//330))
        for grid, cards in self.groups:
            for i, card in enumerate(cards): grid.addWidget(card,i//columns,i%columns)
            for i in range(4): grid.setColumnStretch(i, 1 if i<columns else 0)
            for card in cards: card.setMaximumWidth(max(260, (self.viewport().width()-48)//columns-12))

    def resizeEvent(self, event):
        super().resizeEvent(event); self.reflow()


def stylesheet(theme):
    light = theme == "light"
    base, panel, ink, muted = ("#fafafa","#eeeeee","#141414","#626262") if light else ("#000000","#101010","#f2f2f2","#aaaaaa")
    return f"""
    QWidget#cinemaRoot {{ background:{base};color:{ink}; }}
    QWidget#cinemaRoot QWidget {{ background:transparent;font-size:14px; }}
    QWidget#cinemaRoot QWidget#surfaceContent, QWidget#cinemaRoot QLabel,
    QWidget#cinemaRoot QScrollArea, QWidget#cinemaRoot QScrollArea > QWidget > QWidget {{ background:transparent; }}
    QWidget#cinemaRoot QFrame#card {{background:{panel};border:0;border-radius:22px;}}
    QWidget#cinemaRoot QLabel#cinemaHeading {{font-size:32px;font-weight:750;color:{ink};}}
    QWidget#cinemaRoot QLabel#cinemaHeroTitle {{font-size:44px;font-weight:750;color:{ink};}}
    QWidget#cinemaRoot QLabel#cinemaSection {{font-size:22px;font-weight:700;color:{ink};}}
    QWidget#cinemaRoot QLabel#cardHeading {{font-size:16px;font-weight:700;color:{ink};}}
    QWidget#cinemaRoot QLabel#cinemaMeta {{font-size:14px;color:{ink};background:rgba(100,100,100,45);border:1px solid rgba(150,150,150,35);border-radius:16px;padding:9px 14px;}}
    QWidget#cinemaRoot QLabel#cinemaMuted {{color:{muted};font-size:14px;}}
    QWidget#cinemaRoot QLabel#detailSynopsis {{font-size:16px;color:{ink};}}
    QWidget#cinemaRoot QPushButton {{background:{panel};color:{ink};border:0;border-radius:19px;padding:10px 18px;min-height:20px;}}
    QWidget#cinemaRoot QPushButton:hover {{background:{'#dddddd' if light else '#272727'};}}
    QWidget#cinemaRoot QPushButton#cinemaPrimary,
    QWidget#cinemaRoot QPushButton[cinemaPill="true"]:checked {{background:{ink};color:{base};border-radius:23px;padding:13px 24px;}}
    QWidget#cinemaRoot QPushButton[cinemaPill="true"] {{font-size:15px;min-width:120px;min-height:22px;padding:13px 24px;border-radius:23px;}}
    QWidget#cinemaRoot QPushButton#cinemaNav {{background:transparent;border-radius:0;color:{muted};padding:13px 12px;border-bottom:2px solid transparent;}}
    QWidget#cinemaRoot QPushButton#cinemaNav:checked {{color:{ink};border-bottom:2px solid {ink};}}
    QWidget#cinemaRoot QLineEdit, QWidget#cinemaRoot QComboBox {{background:{panel};color:{ink};border:0;border-radius:20px;padding:10px 16px;min-height:22px;}}
    QWidget#cinemaRoot QComboBox QAbstractItemView {{background:{panel};color:{ink};}}
    QWidget#cinemaRoot QComboBox#cinemaFilter {{background:transparent;border-radius:0;border-bottom:1px solid #666666;padding:7px 8px;}}
    QWidget#cinemaRoot QPushButton#ghost {{background:transparent;border:0;color:{ink};}}
    QWidget#cinemaRoot QPushButton#winMin, QWidget#cinemaRoot QPushButton#winMax,
    QWidget#cinemaRoot QPushButton#winClose {{padding:0;min-height:0;border-radius:4px;background:transparent;}}
    QWidget#cinemaRoot QFrame#detailHero {{background:transparent;}}
    QWidget#cinemaRoot QLabel#pageTitle {{font-size:28px;font-weight:750;color:{ink};}}
    QWidget#cinemaRoot QLabel#pageDescription {{font-size:14px;color:{muted};}}
    QWidget#cinemaRoot QFrame#sidebar {{background:{panel};border:0;border-radius:18px;}}
    QWidget#cinemaRoot QPushButton#navButton {{background:transparent;color:{muted};text-align:left;padding:14px 12px;border-radius:12px;}}
    QWidget#cinemaRoot QPushButton#navButton:checked {{background:#383838;color:white;}}
    QWidget#cinemaRoot QPushButton#profileButton {{background:transparent;text-align:left;padding:10px 4px;border-radius:12px;}}
    """
