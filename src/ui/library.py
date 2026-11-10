"""Poster-grid library with real list counts, filters and stable sorting."""
import math
from PySide6.QtCore import Qt, QRectF, QPointF, QSize, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QPixmapCache
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFrame, QGridLayout, QHBoxLayout,
                              QLabel, QLineEdit, QListWidget, QListWidgetItem, QListView,
                              QMenu, QPushButton, QStyledItemDelegate, QVBoxLayout, QWidget)
from .poster import draw_filled_pixmap
from ..core.media_identity import MediaIdentity, normalized_title


class PosterDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return self.parent().gridSize()

    def paint(self, painter, option, index):
        window = self.parent().window()
        entry = window._artwork.enrich(index.data(Qt.UserRole), priority=5)
        area = QRectF(option.rect).adjusted(7, 5, -7, -33)
        light = window._applied_theme == "light"
        accent = QColor(window.cfg.data.accent)
        p = painter; p.save(); p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen); p.setBrush(QColor("#ded8eb" if light else "#211b32")); p.drawRoundedRect(area, 20, 20)
        cover = entry.get("cover") or ""
        pixmap = QPixmapCache.find(cover) if cover else None
        if pixmap is None:
            pixmap = QPixmap(str(window._cover_cache_path(cover))) if cover else QPixmap()
            if not pixmap.isNull():
                pixmap = pixmap.scaled(640, 960, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                QPixmapCache.insert(cover, pixmap)
        if not pixmap.isNull():
            draw_filled_pixmap(p, area, pixmap, 20)
        elif cover:
            window._request_library_poster(cover)
        clip = QPainterPath(); clip.addRoundedRect(area, 20, 20)
        p.setClipPath(clip, Qt.IntersectClip)
        gradient = QLinearGradient(0, area.top()+area.height()*.42, 0, area.bottom())
        gradient.setColorAt(0, QColor(0,0,0,0)); gradient.setColorAt(1, QColor(0,0,0,238))
        p.fillRect(area, gradient)
        font = QFont(option.font); font.setPixelSize(14); font.setWeight(QFont.DemiBold)
        p.setFont(font); p.setPen(Qt.white)
        title = entry.get("title_english") or entry.get("title") or "Unknown"
        title_rect = area.adjusted(10, area.height()-65, -10, -35)
        p.drawText(title_rect, Qt.AlignCenter, p.fontMetrics().elidedText(title, Qt.ElideRight, int(title_rect.width())))
        font.setPixelSize(12); font.setWeight(QFont.Normal); p.setFont(font)
        count = entry.get("episodes")
        score = entry.get("mean_score")
        text = f"{count} eps" if count else "Episodes TBA"
        p.setPen(QColor("#f5d976") if score else QColor("#dad6e1"))
        meta = area.adjusted(8, area.height()-34, -8, -10)
        if score:
            rating = f"{score/10:.1f}"
            width = p.fontMetrics().horizontalAdvance(text)
            total = width + 29 + p.fontMetrics().horizontalAdvance(rating)
            x = meta.center().x()-total/2
            p.drawText(QRectF(x, meta.top(), width, meta.height()), Qt.AlignVCenter, text)
            star = QPainterPath()
            center = QPointF(x+width+16, meta.center().y())
            for i in range(10):
                angle = -math.pi/2+i*math.pi/5
                radius = 6 if i%2 == 0 else 2.8
                point = center+QPointF(math.cos(angle)*radius, math.sin(angle)*radius)
                star.moveTo(point) if i == 0 else star.lineTo(point)
            star.closeSubpath(); p.fillPath(star, QColor("#f5d976"))
            p.drawText(QRectF(x+width+29,meta.top(),40,meta.height()),Qt.AlignVCenter,rating)
        else:
            p.drawText(meta, Qt.AlignCenter, text)
        badge = QRectF(area.right()-46, area.top()+12, 34,34)
        p.setBrush(QColor("#f4f7f5")); p.setPen(Qt.NoPen); p.drawEllipse(badge)
        p.setPen(QPen(QColor("#17221d"), 1.8)); p.setBrush(Qt.NoBrush)
        c = badge.center(); status = entry.get("library_section")
        if status == "Watching":
            eye = QPainterPath(QPointF(c.x()-8,c.y()))
            eye.quadTo(c.x(),c.y()-10,c.x()+8,c.y()); eye.quadTo(c.x(),c.y()+10,c.x()-8,c.y())
            p.drawPath(eye); p.drawEllipse(c, 2.5,2.5)
        elif status == "Completed":
            p.drawEllipse(c,8,8)
            path=QPainterPath(QPointF(c.x()-4,c.y()));path.lineTo(c.x()-1,c.y()+3);path.lineTo(c.x()+5,c.y()-4);p.drawPath(path)
        elif status == "Paused":
            p.drawLine(QPointF(c.x()-3,c.y()-6),QPointF(c.x()-3,c.y()+6))
            p.drawLine(QPointF(c.x()+3,c.y()-6),QPointF(c.x()+3,c.y()+6))
        elif status == "Dropped":
            p.drawLine(QPointF(c.x()-5,c.y()-5),QPointF(c.x()+5,c.y()+5))
            p.drawLine(QPointF(c.x()+5,c.y()-5),QPointF(c.x()-5,c.y()+5))
        else:
            bookmark=QPainterPath(QPointF(c.x()-5,c.y()-7));bookmark.lineTo(c.x()+5,c.y()-7)
            bookmark.lineTo(c.x()+5,c.y()+7);bookmark.lineTo(c.x(),c.y()+3);bookmark.lineTo(c.x()-5,c.y()+7);bookmark.closeSubpath();p.drawPath(bookmark)
        p.setClipping(False)
        from PySide6.QtWidgets import QStyle
        if option.state & (QStyle.State_Selected | QStyle.State_MouseOver):
            p.setPen(QPen(accent.lighter(125),2));p.setBrush(Qt.NoBrush);p.drawRoundedRect(area,20,20)
        progress = entry.get("progress") or 0
        if status != "Planning":
            p.setPen(QColor("#71657e" if light else "#aea6b8"))
            text = f"{progress:,}/{count:,} watched" if count else f"{progress:,} watched"
            p.drawText(QRectF(area.left()+2,area.bottom()+7,area.width()-4,22),Qt.AlignLeft|Qt.AlignVCenter,text)
        p.restore()


class PosterGrid(QListWidget):
    def __init__(self):
        super().__init__()
        self.setViewMode(QListView.IconMode)
        self.setMovement(QListView.Static)
        self.setResizeMode(QListView.Adjust)
        self.setWrapping(True)
        self.setMouseTracking(True)
        self.setUniformItemSizes(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet("QListWidget { background: transparent; border: none; padding: 0; } "
                           "QListWidget::item { padding: 0; margin: 0; border: none; }")
        self.setItemDelegate(PosterDelegate(self))
        self.setMinimumHeight(280)
        self.setGridSize(QSize(190, 302))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        columns = max(1, min(6, self.viewport().width()//175))
        # Leave room for Qt's icon-mode wrapping/scrollbar layout margins.
        width = max(140, (self.viewport().width()-24)//columns)
        self.setGridSize(QSize(width, int((width-14)*1.5)+38))
        self.doItemsLayout()


class LibraryPanel(QWidget):
    activated = Signal(object)
    refresh_requested = Signal()
    calendar_requested = Signal()

    def __init__(self):
        super().__init__()
        self.entries = []
        self.section = "All"
        self.layout_root = QVBoxLayout(self); self.layout_root.setContentsMargins(0,0,0,0);self.layout_root.setSpacing(16)
        self.bar = QGridLayout();self.bar.setContentsMargins(0,0,0,0);self.bar.setSpacing(12)
        self.tabs = QWidget(); tab_layout=QGridLayout(self.tabs);tab_layout.setContentsMargins(0,0,0,0);tab_layout.setSpacing(8)
        self.buttons = {}
        for section, label in (("All","All"),("Watching","Watching"),("Planning","Plan to watch"),("Completed","Completed")):
            btn=QPushButton(label);btn.setCheckable(True);btn.setProperty("libraryPill",True)
            btn.setFixedHeight(40)
            btn.clicked.connect(lambda checked=False,s=section:self.select_section(s))
            self.buttons[section]=btn;tab_layout.addWidget(btn,0,len(self.buttons)-1)
        self.tools=QWidget();tools=QHBoxLayout(self.tools);tools.setContentsMargins(0,0,0,0);tools.setSpacing(8)
        self.search=QLineEdit();self.search.setPlaceholderText("Search list…");self.search.setClearButtonEnabled(True);self.search.setMinimumWidth(100)
        tools.addWidget(self.search,1)
        self.sort=QComboBox();self.sort.addItems(["Recently added","Title A–Z","Highest score","Newest year","Most episodes"]);tools.addWidget(self.sort)
        self.filters_button=QPushButton("Filters");self.filters_button.setCheckable(True);tools.addWidget(self.filters_button)
        more=QPushButton("More");more.setFixedWidth(78);tools.addWidget(more)
        menu=QMenu(more);menu.addAction("Refresh AniList", self.refresh_requested.emit)
        menu.addAction("Calendar", self.calendar_requested.emit);menu.addAction("Reset filters", self.reset_filters);more.setMenu(menu)
        self.bar.addWidget(self.tabs,0,0);self.bar.addWidget(self.tools,0,1);self.bar.setColumnStretch(1,1)
        self.layout_root.addLayout(self.bar)
        self.filter_box=QWidget();filters=QHBoxLayout(self.filter_box);filters.setContentsMargins(0,0,0,0)
        self.genre=QComboBox();self.year=QComboBox();self.score=QComboBox();self.status=QComboBox()
        self.genre.addItem("All genres");self.year.addItem("All years")
        self.score.addItems(["Any score","Score 7+","Score 8+","Score 9+"])
        self.status.addItems(["Any status","Watching","Planning","Completed","Paused","Dropped"])
        for control in (self.genre,self.year,self.score,self.status):filters.addWidget(control)
        self.layout_root.addWidget(self.filter_box);self.filter_box.hide()
        self.filters_button.toggled.connect(self.filter_box.setVisible)
        self.empty=QLabel("Add an AniList account and refresh to load your library.");self.empty.setObjectName("subLabel");self.empty.setWordWrap(True)
        self.layout_root.addWidget(self.empty)
        self.grid=PosterGrid();self.layout_root.addWidget(self.grid,1)
        self.grid.itemActivated.connect(lambda item:self.activated.emit(item.data(Qt.UserRole)))
        self.search.textChanged.connect(self.refresh)
        for control in (self.sort,self.genre,self.year,self.score,self.status):control.currentIndexChanged.connect(self.refresh)
        self.buttons["All"].setChecked(True)

    def configure(self, light):
        ink="#25202f" if light else "#edeaf2";base="#e8e3ed" if light else "#201c2b"
        selected="#272130" if light else "#f1f1f3";selected_ink="#ffffff" if light else "#151318"
        for button in self.buttons.values():
            button.setStyleSheet(f'QPushButton {{ background:{base};color:{ink};border:0;border-radius:20px;padding:0 14px; }} '
                                 f'QPushButton:checked {{ background:{selected};color:{selected_ink}; }}')
        self.grid.viewport().update()

    def select_section(self, section):
        self.section=section
        for key,button in self.buttons.items():button.setChecked(key==section)
        self.status.setCurrentIndex(0)
        self.refresh()

    def set_sections(self, sections, message=""):
        entries=[dict(e,library_section=section) for section,rows in sections.items() for e in rows]
        resolver=MediaIdentity(entries);unique={}
        for entry in entries:
            key=resolver.key(entry)
            unique.setdefault(key,dict(entry,title=resolver.title(entry)))
        entries=list(unique.values())
        if entries==self.entries:
            self.grid.viewport().update()
            if not entries and message:self.empty.setText(message)
            return
        self.entries=entries
        labels={"All":"All","Watching":"Watching","Planning":"Plan to watch","Completed":"Completed"}
        for section,button in self.buttons.items():
            count=len(entries) if section=="All" else sum(e["library_section"]==section for e in entries)
            button.setText(f"{labels[section]}  {count}")
        for control,values,placeholder in ((self.genre,sorted({g for e in entries for g in e.get("genres") or []}),"All genres"),
                                           (self.year,sorted({str(e["season_year"]) for e in entries if e.get("season_year")},reverse=True),"All years")):
            current=control.currentText();control.blockSignals(True);control.clear();control.addItems([placeholder,*values])
            control.setCurrentIndex(max(0,control.findText(current)));control.blockSignals(False)
        self.refresh()
        if not entries and message:
            self.empty.setText(message)

    def refresh(self, *_):
        query=normalized_title(self.search.text())
        minimum=(0,70,80,90)[self.score.currentIndex()]
        rows=[e for e in self.entries if (self.section=="All" or e["library_section"]==self.section)
              and (self.status.currentIndex()==0 or e["library_section"]==self.status.currentText())
              and (self.genre.currentIndex()==0 or self.genre.currentText() in (e.get("genres") or []))
              and (self.year.currentIndex()==0 or str(e.get("season_year"))==self.year.currentText())
              and (e.get("mean_score") or 0)>=minimum
              and query in normalized_title(" ".join([e.get("title") or "",e.get("title_english") or "",*(e.get("aliases") or []),*(e.get("genres") or [])]))]
        mode=self.sort.currentIndex()
        if mode==1:rows.sort(key=lambda e:normalized_title(e.get("title_english") or e.get("title")))
        else:
            field=("created_at","title","mean_score","season_year","episodes")[mode]
            rows.sort(key=lambda e:e.get(field) or 0,reverse=True)
        selected=self.grid.currentItem().data(Qt.UserRole) if self.grid.currentItem() else None
        resolver=MediaIdentity(self.entries);selected_key=resolver.key(selected) if selected else None
        scroll=self.grid.verticalScrollBar().value();self.grid.clear()
        for entry in rows:
            item=QListWidgetItem();item.setData(Qt.UserRole,entry)
            item.setToolTip(f"{entry.get('title_english') or entry.get('title')} · {entry['library_section']}")
            self.grid.addItem(item)
            if selected_key and resolver.key(entry)==selected_key:self.grid.setCurrentItem(item)
        self.grid.verticalScrollBar().setValue(scroll)
        self.empty.setVisible(not rows)
        self.empty.setText("No matching anime. Adjust your filters." if self.entries else "Add an AniList account and refresh to load your library.")

    def reset_filters(self):
        self.search.clear()
        for control in (self.genre,self.year,self.score,self.status):control.setCurrentIndex(0)
        self.sort.setCurrentIndex(0);self.select_section("All")

    def resizeEvent(self,event):
        for i, button in enumerate(self.buttons.values()):
            self.tabs.layout().removeWidget(button)
            self.tabs.layout().addWidget(button, i//2 if self.width()<620 else 0, i%2 if self.width()<620 else i)
        self.bar.removeWidget(self.tabs);self.bar.removeWidget(self.tools)
        if self.width()<1100:
            self.bar.addWidget(self.tabs,0,0,1,2);self.bar.addWidget(self.tools,1,0,1,2)
        else:
            self.bar.addWidget(self.tabs,0,0);self.bar.addWidget(self.tools,0,1)
        super().resizeEvent(event)
