"""Poster-grid library with real list counts, filters and stable sorting."""
import math
import time
from collections import OrderedDict
from PySide6.QtCore import Qt, QRect, QRectF, QPointF, QSize, Signal, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QIcon
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFrame, QGridLayout, QHBoxLayout,
                              QLabel, QLineEdit, QListWidget, QListWidgetItem, QListView,
                              QMenu, QPushButton, QStyledItemDelegate, QStyleOptionViewItem, QStyle, QVBoxLayout, QWidget)
from .poster import draw_filled_pixmap
from ..core.media_identity import MediaIdentity, normalized_title


STATUSES = (("CURRENT", "Watching"), ("REPEATING", "Rewatching"), ("PLANNING", "Plan to watch"),
            ("COMPLETED", "Completed"), ("PAUSED", "On hold"), ("DROPPED", "Dropped"))
SECTIONS = {"CURRENT": "Watching", "REPEATING": "Watching", "PLANNING": "Planning",
            "COMPLETED": "Completed", "PAUSED": "Paused", "DROPPED": "Dropped"}


def draw_status(p, c, status):
    if status in ("Watching", "CURRENT"):
        path = QPainterPath(c+QPointF(-4,-7))
        path.lineTo(c+QPointF(7,0)); path.lineTo(c+QPointF(-4,7)); path.closeSubpath()
        p.drawPath(path)
    elif status == "REPEATING":
        p.drawArc(QRectF(c.x()-7,c.y()-7,14,14),30*16,270*16)
        p.drawLine(c+QPointF(7,-3),c+QPointF(7,-8));p.drawLine(c+QPointF(7,-3),c+QPointF(2,-3))
    elif status in ("Completed", "COMPLETED"):
        path=QPainterPath(c+QPointF(-6,0));path.lineTo(c+QPointF(-1,5));path.lineTo(c+QPointF(7,-5));p.drawPath(path)
    elif status in ("Paused", "PAUSED"):
        for x in (-3,3):p.drawLine(c+QPointF(x,-6),c+QPointF(x,6))
    elif status == "REMOVE":
        p.drawRect(QRectF(c.x()-5,c.y()-4,10,11))
        p.drawLine(c+QPointF(-7,-6),c+QPointF(7,-6));p.drawLine(c+QPointF(-2,-8),c+QPointF(2,-8))
        for x in (-2,2):p.drawLine(c+QPointF(x,-1),c+QPointF(x,4))
    elif status in ("Dropped", "DROPPED"):
        p.drawLine(c+QPointF(-5,-5),c+QPointF(5,5));p.drawLine(c+QPointF(5,-5),c+QPointF(-5,5))
    elif status in ("Planning", "PLANNING"):
        bookmark=QPainterPath(c+QPointF(-5,-7));bookmark.lineTo(c+QPointF(5,-7))
        bookmark.lineTo(c+QPointF(5,7));bookmark.lineTo(c+QPointF(0,3));bookmark.lineTo(c+QPointF(-5,7));bookmark.closeSubpath();p.drawPath(bookmark)
    else:
        p.drawLine(c+QPointF(-6,0),c+QPointF(6,0)); p.drawLine(c+QPointF(0,-6),c+QPointF(0,6))


def status_icon(status, light):
    pixmap = QPixmap(40,40);pixmap.fill(Qt.transparent);pixmap.setDevicePixelRatio(2)
    p=QPainter(pixmap);p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor("#dc586a" if status=="REMOVE" else "#5d536c" if light else "#c6c1ce"),1.7,Qt.SolidLine,Qt.RoundCap,Qt.RoundJoin))
    draw_status(p,QPointF(10,10),status);p.end()
    return QIcon(pixmap)


class StatusMenu(QMenu):
    def __init__(self, parent, light):
        super().__init__(parent)
        self.light = light
        self.setMinimumWidth(230)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self);painter.setRenderHint(QPainter.Antialiasing)
        for action in self.actions():
            if action.isChecked():
                rect = self.actionGeometry(action)
                center = QPointF(rect.right()-16, rect.center().y())
                color = QColor("#332b40" if self.light and action != self.activeAction() else "#f3eff9")
                painter.setPen(QPen(color, 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                path=QPainterPath(center+QPointF(-4,0));path.lineTo(center+QPointF(-1,3));path.lineTo(center+QPointF(5,-4))
                painter.drawPath(path)


class PosterDelegate(QStyledItemDelegate):
    def __init__(self, parent):
        super().__init__(parent)
        self.frames = OrderedDict()
        self.frame_bytes = 0
        self.reveals=OrderedDict()
        self.reveal_timer=QTimer(self);self.reveal_timer.setInterval(16)
        self.reveal_timer.timeout.connect(self._reveal_tick)

    def _reveal_tick(self):
        self.parent().viewport().update()
        if not any(time.monotonic()-started<.2 for started in self.reveals.values()):
            self.reveal_timer.stop()

    def sizeHint(self, option, index):
        return self.parent().gridSize()

    def paint(self, painter, option, index):
        window = self.parent().window()
        entry = window._artwork.enrich(index.data(Qt.UserRole), priority=5)
        area = QRectF(option.rect).adjusted(7, 5, -7, -33)
        cover = entry.get("cover") or ""
        dpr = painter.device().devicePixelRatioF()
        pixmap = window._images.get(window._cover_cache_path(cover), math.ceil(area.width()*dpr), math.ceil(area.height()*dpr)) if cover else QPixmap()
        if pixmap.isNull() and cover:
            window._request_library_poster(cover)
        key = (tuple(entry.get(field) for field in ("title", "title_english", "cover", "progress", "episodes", "mean_score", "status", "library_section","relation","season_year")),
               option.rect.width(),option.rect.height(),dpr,pixmap.cacheKey(),window._applied_theme,window.cfg.data.ui_layout,window.cfg.data.accent,option.font.key())
        if key not in self.frames:
            frame = QPixmap(math.ceil(option.rect.width()*dpr),math.ceil(option.rect.height()*dpr))
            frame.setDevicePixelRatio(dpr);frame.fill(Qt.transparent)
            local = QStyleOptionViewItem(option);local.rect=QRect(0,0,option.rect.width(),option.rect.height())
            local.state &= ~(QStyle.State_Selected|QStyle.State_MouseOver)
            canvas=QPainter(frame);self._paint_card(canvas,local,entry,pixmap);canvas.end()
            cost=frame.width()*frame.height()*4
            while self.frames and self.frame_bytes+cost>48*1024*1024:
                _,old=self.frames.popitem(last=False)
                self.frame_bytes-=old.width()*old.height()*4
            self.frames[key]=frame;self.frame_bytes+=cost
        self.frames.move_to_end(key)
        painter.save()
        if window.cfg.data.ui_layout=="cinema" and window.cfg.data.smooth_animations and not pixmap.isNull():
            reveal=(cover,pixmap.cacheKey())
            if reveal not in self.reveals:
                self.reveals[reveal]=time.monotonic()
                while len(self.reveals)>256:self.reveals.popitem(last=False)
                self.reveal_timer.start()
            opacity=min(1,(time.monotonic()-self.reveals[reveal])/.2)
            painter.setOpacity(opacity)
        painter.drawPixmap(option.rect.topLeft(),self.frames[key]);painter.restore()
        if option.state & (QStyle.State_Selected|QStyle.State_MouseOver):
            painter.save();painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(QPen(QColor(window.cfg.data.accent).lighter(125),2));painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(area,20,20);painter.restore()

    def _paint_card(self, painter, option, entry, pixmap):
        window = self.parent().window()
        area = QRectF(option.rect).adjusted(7, 5, -7, -33)
        light = window._applied_theme == "light"
        cinema = window.cfg.data.ui_layout == "cinema"
        accent = QColor(window.cfg.data.accent)
        p = painter; p.save(); p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen); p.setBrush(QColor("#ded8eb" if light else "#202020")); p.drawRoundedRect(area, 20, 20)
        if not pixmap.isNull():
            draw_filled_pixmap(p, area, pixmap, 20)
        elif cinema:
            from .details import artwork_placeholder
            artwork_placeholder(p,area,light)
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
        if cinema and entry.get("genres"):
            text = f"{entry['genres'][0][:9]} · {count or '—'} eps"
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
        p.save()
        frost_clip = QPainterPath(); frost_clip.addRoundedRect(badge, 12, 12)
        p.setClipPath(frost_clip, Qt.IntersectClip)
        if not pixmap.isNull():
            blurred = pixmap.scaled(18, 28, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            draw_filled_pixmap(p, area, blurred, 20)
        sheen = QLinearGradient(badge.topLeft(), badge.bottomRight())
        sheen.setColorAt(0, QColor(255,255,255,215)); sheen.setColorAt(1, QColor(235,240,247,165))
        p.fillRect(badge, sheen); p.restore()
        p.setBrush(Qt.NoBrush); p.setPen(QPen(QColor(255,255,255,150),1)); p.drawRoundedRect(badge,12,12)
        p.setPen(QPen(QColor("#20232d"), 1.8,Qt.SolidLine,Qt.RoundCap,Qt.RoundJoin)); p.setBrush(Qt.NoBrush)
        status = entry.get("library_section")
        if cinema and (entry.get("status") in ("CURRENT","REPEATING") or status=="Watching"):
            c=badge.center();eye=QPainterPath()
            eye.moveTo(c.x()-9,c.y());eye.quadTo(c.x(),c.y()-10,c.x()+9,c.y());eye.quadTo(c.x(),c.y()+10,c.x()-9,c.y())
            p.drawPath(eye);p.drawEllipse(c,3,3)
        else:
            draw_status(p,badge.center(),entry.get("status") or status)
        p.setClipping(False)
        from PySide6.QtWidgets import QStyle
        if option.state & (QStyle.State_Selected | QStyle.State_MouseOver):
            p.setPen(QPen(accent.lighter(125),2));p.setBrush(Qt.NoBrush);p.drawRoundedRect(area,20,20)
        progress = entry.get("progress") or 0
        if status and status != "Planning":
            p.setPen(QColor("#71657e" if light else "#aeaeae"))
            text = f"{progress:,}/{count:,} watched" if count else f"{progress:,} watched"
            p.drawText(QRectF(area.left()+2,area.bottom()+7,area.width()-4,22),Qt.AlignLeft|Qt.AlignVCenter,text)
        elif cinema and entry.get("relation"):
            p.setPen(QColor("#666666" if light else "#bbbbbb"))
            label=entry["relation"]+(f" · {entry['season_year']}" if entry.get("season_year") else "")
            p.drawText(QRectF(area.left()+2,area.bottom()+7,area.width()-4,22),Qt.AlignLeft|Qt.AlignVCenter,label)
        p.restore()


class PosterGrid(QListWidget):
    status_requested = Signal(object, object)
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

    def badge_rect(self, item):
        area = QRectF(self.visualItemRect(item)).adjusted(7,5,-7,-33)
        return QRectF(area.right()-46,area.top()+12,34,34)

    def mousePressEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        if item and event.button() == Qt.LeftButton and self.badge_rect(item).contains(event.position()):
            self.setCurrentItem(item)
            self.status_requested.emit(item.data(Qt.UserRole), self.viewport().mapToGlobal(self.badge_rect(item).bottomLeft().toPoint()))
            event.accept()
            return
        super().mousePressEvent(event)

    def contextMenuEvent(self, event):
        item = self.itemAt(event.pos()) or self.currentItem()
        if item:
            self.status_requested.emit(item.data(Qt.UserRole), event.globalPos())
            event.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Space and self.currentItem():
            item=self.currentItem()
            self.status_requested.emit(item.data(Qt.UserRole), self.viewport().mapToGlobal(self.badge_rect(item).bottomLeft().toPoint()))
            event.accept();return
        super().keyPressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reflow()

    def reflow(self):
        cinema = hasattr(self.window(),"cfg") and self.window().cfg.data.ui_layout == "cinema"
        columns = max(1, min(8, self.viewport().width()//(210 if cinema else 175)))
        # Leave room for Qt's icon-mode wrapping/scrollbar layout margins.
        width = max(140, (self.viewport().width()-24)//columns)
        self.setGridSize(QSize(width, int((width-14)*1.5)+38))
        self.doItemsLayout()


class LibraryPanel(QWidget):
    activated = Signal(object)
    refresh_requested = Signal()
    calendar_requested = Signal()
    status_changed = Signal(object, str)

    def __init__(self):
        super().__init__()
        self.entries = []
        self.section = "All"
        self.pending_edits = set()
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
        self.notice=QLabel();self.notice.setObjectName("subLabel");self.notice.setWordWrap(True);self.notice.hide()
        self.layout_root.addWidget(self.notice)
        self.grid=PosterGrid();self.layout_root.addWidget(self.grid,1)
        self.grid.status_requested.connect(self.show_status_menu)
        self.grid.itemActivated.connect(lambda item:self.activated.emit(item.data(Qt.UserRole)))
        self.grid.itemClicked.connect(lambda item:self.activated.emit(item.data(Qt.UserRole)))
        self.search.textChanged.connect(self.refresh)
        for control in (self.sort,self.genre,self.year,self.score,self.status):control.currentIndexChanged.connect(self.refresh)
        self.buttons["All"].setChecked(True)

    def show_status_menu(self, entry, position):
        from ..core.media_identity import media_id
        window=self.window();account=window.controller.active_anilist_account
        light=window._applied_theme=="light"
        menu=StatusMenu(self.grid,light)
        menu.setStyleSheet(f"QMenu {{background:{'#faf8fd' if light else '#19171e'};color:{'#302a39' if light else '#e5e1eb'};border:1px solid #635770;padding:8px;font-size:14px;}} "
                          "QMenu::item {padding:12px 28px 12px 12px;} QMenu::item:selected {background:#6d38a1;color:white;} QMenu::separator {height:1px;background:#635770;margin:8px;}")
        current=entry.get("status") or next((s for s,section in SECTIONS.items() if section==entry.get("library_section")),"")
        def change(status):
            if window.controller.active_anilist_account == account:
                self.status_changed.emit(entry,status)
        for status,label in STATUSES:
            action=menu.addAction(status_icon(status,light),label)
            action.setData(status);action.setCheckable(True);action.setChecked(status==current)
            action.setEnabled(media_id(entry) not in self.pending_edits)
            action.triggered.connect(lambda checked=False,s=status:change(s))
        menu.addSeparator()
        remove=menu.addAction(status_icon("REMOVE",light),"Remove from library")
        remove.setData("REMOVE");remove.setEnabled(media_id(entry) not in self.pending_edits)
        remove.triggered.connect(lambda:change("REMOVE"))
        menu.aboutToHide.connect(menu.deleteLater)
        self._status_menu=menu
        menu.popup(position)

    def configure(self, light):
        ink="#25202f" if light else "#edeaf2";base="#e8e3ed" if light else "#201c2b"
        selected="#272130" if light else "#f1f1f3";selected_ink="#ffffff" if light else "#151318"
        if self.window().cfg.data.ui_layout == "cinema" and not light:
            ink="#eeeeee";base="#121212";selected="#f1f1f1";selected_ink="#111111"
        for button in self.buttons.values():
            button.setStyleSheet(f'QPushButton {{ background:{base};color:{ink};border:0;border-radius:20px;padding:0 14px; }} '
                                 f'QPushButton:checked {{ background:{selected};color:{selected_ink}; }}')
        self.grid.viewport().update()
        self.grid.reflow()

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
