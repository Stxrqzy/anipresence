"""In-app details with bounded episode pages and asynchronous metadata/artwork."""
import threading
from PySide6.QtCore import Qt, QRectF, Signal, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QColor, QPainter, QLinearGradient, QPixmap, QPen, QPainterPath
from PySide6.QtWidgets import (QWidget, QScrollArea, QFrame, QVBoxLayout, QHBoxLayout,
                              QGridLayout, QLabel, QPushButton, QComboBox, QSizePolicy)
from ..anime.details import load_details
from .poster import draw_filled_pixmap


class ImageReveal:
    """One short reveal per arriving image; respects the motion preference."""
    def __init__(self, widget):
        self.widget=widget;self.key=None
        self.animation=QVariantAnimation(widget);self.animation.setDuration(200)
        self.animation.setStartValue(0.0);self.animation.setEndValue(1.0)
        self.animation.setEasingCurve(QEasingCurve.OutCubic)
        self.animation.valueChanged.connect(lambda _:widget.update())

    def opacity(self, key):
        window=self.widget.window()
        enabled=hasattr(window,"cfg") and window.cfg.data.ui_layout=="cinema" and window.cfg.data.smooth_animations
        if key!=self.key:
            self.key=key;self.animation.stop()
            if enabled:self.animation.start()
        return float(self.animation.currentValue() or 0) if enabled and self.animation.state()==QVariantAnimation.Running else 1.0


def artwork_placeholder(painter, rect, light=False, caption=""):
    painter.save();painter.setRenderHint(QPainter.Antialiasing)
    gradient=QLinearGradient(rect.topLeft(),rect.bottomRight())
    gradient.setColorAt(0,QColor("#e7e7e7" if light else "#222222"))
    gradient.setColorAt(1,QColor("#f4f4f4" if light else "#101010"))
    painter.setPen(Qt.NoPen);painter.setBrush(gradient);painter.drawRoundedRect(rect,12,12)
    painter.setBrush(QColor("#d2d2d2" if light else "#303030"))
    cx,cy=rect.center().x(),rect.center().y()
    painter.drawRoundedRect(QRectF(cx-24,cy-24,48,32),8,8)
    painter.drawRoundedRect(QRectF(cx-36,cy+17,72,5),2,2)
    if caption:
        painter.setPen(QColor("#666666" if light else "#999999"))
        painter.drawText(rect.adjusted(8,rect.height()*.65,-8,-4),Qt.AlignCenter|Qt.TextWordWrap,caption)
    painter.restore()


def watched_badge(painter, rect):
    """Frosted completed check, shared by episode stills and history artwork."""
    painter.save();painter.setOpacity(1);painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor(255,255,255,170),1));painter.setBrush(QColor(245,248,252,220))
    painter.drawRoundedRect(rect,10,10)
    c=rect.center();path=QPainterPath()
    path.moveTo(c.x()-7,c.y());path.lineTo(c.x()-2,c.y()+5);path.lineTo(c.x()+8,c.y()-6)
    painter.setPen(QPen(QColor("#20232d"),2.4,Qt.SolidLine,Qt.RoundCap,Qt.RoundJoin))
    painter.setBrush(Qt.NoBrush);painter.drawPath(path);painter.restore()


class Artwork(QWidget):
    """Transparent fitted logos, or cropped artwork, using the shared decoded cache."""
    def __init__(self, fit=False):
        super().__init__()
        self.url = ""
        self.fallback_url = ""
        self.using_fallback = False
        self.watched = False
        self.fit = fit
        self.reveal=ImageReveal(self)
        self.setObjectName("surfaceContent")

    def pixmap(self):
        window = self.window()
        self.using_fallback=False
        for url in (self.url,self.fallback_url):
            if not url:continue
            pm = window._images.get(window._cover_cache_path(url), 960, 540)
            if pm.isNull():window._request_library_poster(url)
            else:
                self.using_fallback=url!=self.url
                return pm
        return QPixmap()

    def paintEvent(self, event):
        pm = self.pixmap()
        cinema=hasattr(self.window(),"cfg") and self.window().cfg.data.ui_layout=="cinema"
        if pm.isNull():
            if not self.fit:
                p = QPainter(self)
                if cinema:
                    loading=self.url and self.url not in getattr(self.window(),"_poster_failed",set())
                    artwork_placeholder(p,QRectF(self.rect()),self.window()._applied_theme=="light",
                                        "Loading artwork…" if loading else "No image available")
                else:
                    p.setPen(QColor("#888888"))
                    p.drawText(self.rect(), Qt.AlignCenter, "Artwork unavailable")
                if self.watched:watched_badge(p,QRectF(self.width()-42,10,32,32))
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        if cinema and not self.fit:artwork_placeholder(p,QRectF(self.rect()),self.window()._applied_theme=="light")
        p.setOpacity(self.reveal.opacity((self.url,self.fallback_url,pm.cacheKey())))
        if self.fit:
            size = pm.size().scaled(self.size(), Qt.KeepAspectRatio)
            p.drawPixmap(QRectF(0, (self.height()-size.height())/2, size.width(), size.height()), pm, QRectF(pm.rect()))
        else:
            draw_filled_pixmap(p, QRectF(self.rect()), pm, 12)
        if cinema and self.using_fallback:
            p.setOpacity(1);p.fillRect(QRectF(0,self.height()-24,self.width(),24),QColor(0,0,0,150))
            p.setPen(Qt.white);p.drawText(QRectF(4,self.height()-24,self.width()-8,24),Qt.AlignCenter,"Series artwork")
        if self.watched:watched_badge(p,QRectF(self.width()-42,10,32,32))


class Hero(QFrame):
    def __init__(self):
        super().__init__()
        self.url = ""
        self.setObjectName("detailHero")

    def paintEvent(self, event):
        p = QPainter(self)
        rect = QRectF(self.rect())
        window = self.window()
        light = window._applied_theme == "light"
        base = QColor("#f4f4f4" if light else "#000000" if window._applied_theme == "oled" else "#141414")
        p.fillRect(rect, base)
        if self.url:
            pm = window._images.get(window._cover_cache_path(self.url), 1600, 800)
            if pm.isNull():
                window._request_library_poster(self.url)
            else:
                draw_filled_pixmap(p, rect, pm, 14)
        veil = QLinearGradient(0, 0, self.width(), 0)
        base.setAlpha(240); veil.setColorAt(0, base)
        base.setAlpha(200); veil.setColorAt(.55, base)
        base.setAlpha(100); veil.setColorAt(1, base)
        p.fillRect(rect, veil)


def text(value="", name="subLabel"):
    label = QLabel(value)
    label.setObjectName(name)
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    return label


class DetailsPage(QScrollArea):
    result_ready = Signal(int, object)

    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.entry = {}
        self.generation = 0
        self.history = []
        self.cards = []
        self.setObjectName("scroller")
        host = QWidget(); host.setObjectName("surfaceContent")
        self.body = QVBoxLayout(host); self.body.setSpacing(18)
        self.setWidget(host)
        bar = QHBoxLayout()
        back = QPushButton("← Back"); back.setObjectName("ghost"); back.clicked.connect(self.go_back)
        self.notice = text()
        self.retry = QPushButton("Retry"); self.retry.clicked.connect(lambda: self.open(self.entry, remember=False))
        bar.addWidget(back); bar.addWidget(self.notice, 1); bar.addWidget(self.retry)
        self.body.addLayout(bar)
        self.hero = Hero()
        hero_layout = QHBoxLayout(self.hero); hero_layout.setContentsMargins(24, 28, 24, 28); hero_layout.setSpacing(28)
        self.poster = Artwork(); self.poster.setFixedSize(180, 270); hero_layout.addWidget(self.poster, 0, Qt.AlignTop)
        info = QVBoxLayout()
        self.logo = Artwork(fit=True); self.logo.setFixedHeight(100); self.logo.hide()
        self.title = text("", "titleLabel")
        self.metadata = text()
        self.synopsis = text("", "detailSynopsis")
        self.synopsis.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.expand = QPushButton("More"); self.expand.setObjectName("ghost")
        self.expand.clicked.connect(self.toggle_synopsis)
        self.expanded = False
        self.watch = QPushButton("Watch"); self.watch.setObjectName("primaryAction")
        self.watch.clicked.connect(lambda: self.window()._watch_anime(self.entry))
        for widget in (self.logo, self.title, self.metadata, self.synopsis, self.expand, self.watch):
            info.addWidget(widget)
        info.addStretch()
        hero_layout.addLayout(info, 1); self.body.addWidget(self.hero)
        episode_bar = QHBoxLayout()
        episode_bar.addWidget(text("Episodes", "cardHeading"))
        self.ranges = QComboBox(); self.ranges.currentIndexChanged.connect(self.render_episodes)
        episode_bar.addStretch(); episode_bar.addWidget(self.ranges)
        self.body.addLayout(episode_bar)
        self.episode_note = text(); self.body.addWidget(self.episode_note)
        self.episodes_layout = QGridLayout(); self.episodes_layout.setSpacing(12); self.body.addLayout(self.episodes_layout)
        self.body.addWidget(text("Related anime", "cardHeading"))
        self.related_host = QWidget(); self.related_host.setObjectName("surfaceContent")
        self.related_layout = QGridLayout(self.related_host); self.body.addWidget(self.related_host)
        self.body.addStretch()
        self.result_ready.connect(self.receive)

    def open(self, entry, remember=True):
        if remember and self.entry:
            self.history.append(dict(self.entry))
            self.history = self.history[-20:]
        self.entry = dict(entry)
        self.generation += 1
        generation = self.generation
        self.expanded = False
        self.notice.setText("Loading anime details…"); self.retry.hide()
        self.render()
        self.verticalScrollBar().setValue(0)
        signal = self.result_ready
        hide_adult = self.window().cfg.data.hide_adult_content
        def run():
            try:
                result = load_details(entry, hide_adult)
            except Exception:
                result = {"error": "Could not load details. Check your connection and retry."}
            try:
                signal.emit(generation, result)
            except RuntimeError:
                pass
        threading.Thread(target=run, daemon=True, name="anime-details").start()

    def receive(self, generation, result):
        if generation != self.generation:
            return
        if result.get("error"):
            self.notice.setText(result["error"]); self.retry.show()
            return
        self.entry.update(result)
        self.notice.setText("Anime details · AniList")
        self.render()

    def render(self):
        self.title.setText(self.entry.get("title") or "Unknown title")
        self.watch.setText(f"Continue · Episode {self.entry['episode']}" if self.entry.get("episode") else "Watch")
        e = self.entry
        self.metadata.setText("  ·  ".join(str(v) for v in (
            f"Score {e['mean_score']/10:g}" if e.get("mean_score") else "",
            e.get("season_year"), e.get("format"), f"{e['episodes']} episodes" if e.get("episodes") else "",
            f"{e['duration']} min" if e.get("duration") else "",
            ", ".join(e.get("genres") or []), ", ".join(e.get("studios") or [])) if v))
        self.render_synopsis()
        self.ranges.blockSignals(True); self.ranges.clear()
        count = int(e.get("episodes") or e.get("episodes_total") or max(0, (e.get("next_episode") or 1)-1))
        self.episode_count = count
        for start in range(1, count+1, 12):
            self.ranges.addItem(f"{start}–{min(count, start+11)}", start)
        self.ranges.blockSignals(False)
        self.ranges.setVisible(count > 12)
        self.render_episodes()
        self.clear_layout(self.related_layout)
        for i, related in enumerate(e.get("related") or []):
            card = QFrame(); card.setObjectName("card")
            layout = QVBoxLayout(card)
            art = Artwork(); art.url = related.get("cover") or ""; art.setFixedHeight(170)
            button = QPushButton(related["title"]); button.setObjectName("ghost")
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            button.setToolTip(related["title"])
            button.clicked.connect(lambda checked=False, row=related: self.open(row))
            layout.addWidget(art); layout.addWidget(text(related["relation"])); layout.addWidget(button)
            self.related_layout.addWidget(card, i//self.columns(), i%self.columns())
        if not e.get("related"):
            self.related_layout.addWidget(text("No related anime available."), 0, 0)
        self.refresh_artwork()

    @staticmethod
    def clear_layout(layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def render_synopsis(self):
        description = self.entry.get("description") or "Synopsis unavailable."
        self.synopsis.setText(description if self.expanded else description[:420] + ("…" if len(description) > 420 else ""))
        self.expand.setVisible(len(description) > 420)
        self.expand.setText("Less" if self.expanded else "More")

    def toggle_synopsis(self):
        self.expanded = not self.expanded
        self.render_synopsis()

    def render_episodes(self):
        self.clear_layout(self.episodes_layout); self.cards = []
        self.episode_note.setText("Episode artwork and descriptions from TMDB." if self.window().cfg.data.tmdb_credential
                                  else "Add a TMDB credential in Settings → Artwork & metadata for episode images, descriptions and title logos.")
        start = self.ranges.currentData()
        if start is None:
            self.episodes_layout.addWidget(text("Episode information is not available yet."), 0, 0)
            return
        for i, number in enumerate(range(start, min(self.episode_count+1, start+12))):
            card = QFrame(); card.setObjectName("card")
            layout = QVBoxLayout(card); layout.setSpacing(8)
            art = Artwork(); art.setFixedHeight(130)
            title = text(f"Episode {number}", "cardHeading")
            description = text("Description unavailable.")
            description.setMaximumHeight(58)
            watch = QPushButton(f"Watch episode {number}"); watch.setObjectName("ghost")
            watch.clicked.connect(lambda checked=False, ep=number: self.watch_episode(ep))
            for widget in (art, title, description, watch):
                layout.addWidget(widget)
            self.cards.append((number, art, title, description, card))
            self.episodes_layout.addWidget(card, i//self.columns(), i%self.columns())
        self.refresh_artwork()

    def watch_episode(self, number):
        entry = dict(self.entry, episode=number)
        # Only resume a saved URL when it belongs to this exact episode.
        if str(number) != str(self.entry.get("episode")):
            entry["url"] = ""
        self.window()._watch_anime(entry)

    def refresh_artwork(self):
        if not self.entry:
            return
        e = self.window()._artwork.enrich(self.entry, priority=0)
        self.hero.url = e.get("backdrop") or e.get("banner_url") or ""
        self.poster.url = e.get("cover") or e.get("cover_url") or ""
        self.logo.url = e.get("title_logo") or ""
        has_logo = bool(self.logo.url) and not self.logo.pixmap().isNull()
        self.logo.setVisible(has_logo); self.title.setVisible(not has_logo)
        for number, art, title, description, card in self.cards:
            request=self.window().cfg.data.ui_layout!="cinema" or (self.isVisible() and not art.visibleRegion().isEmpty())
            row = self.window()._artwork.enrich(dict(self.entry, episode=number, episode_title=""), episode=True, priority=1,request=request)
            art.url = row.get("episode_art") or ""
            title.setText(f"{number}. {row.get('episode_title') or 'Episode '+str(number)}")
            desc = row.get("episode_description") or "Description unavailable."
            description.setText(desc[:150] + ("…" if len(desc) > 150 else ""))
            card.setToolTip(desc)
        self.hero.update()
        for image in self.findChildren(Artwork):
            image.update()

    def columns(self):
        return max(1, min(3, self.viewport().width()//280))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.poster.setVisible(self.viewport().width() >= 700)
        for i, (_, _, _, _, card) in enumerate(self.cards):
            self.episodes_layout.addWidget(card, i//self.columns(), i%self.columns())
        related = [self.related_layout.itemAt(i).widget() for i in range(self.related_layout.count())]
        for i, card in enumerate(related):
            self.related_layout.addWidget(card, i//self.columns(), i%self.columns())

    def go_back(self):
        if self.history:
            self.open(self.history.pop(), remember=False)
        else:
            self.generation += 1
            self.window()._on_tab_changed(self.window().tabbar.currentIndex())
