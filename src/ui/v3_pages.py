"""Data-backed History, Activity and Statistics pages for the v3 shell."""
import csv
import time
from collections import defaultdict
from datetime import date, datetime, timedelta

from PySide6.QtCore import Qt, QRectF, QPointF, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QLinearGradient
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QListWidgetItem, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)
from .anime_list import AnimeList, AnimeDelegate, DETAIL_ROLE
from ..core.watchlog import format_hours


def label(text, name="subLabel"):
    widget = QLabel(text)
    widget.setObjectName(name)
    widget.setWordWrap(True)
    return widget


def button(text, slot):
    widget = QPushButton(text)
    widget.setObjectName("ghost")
    widget.clicked.connect(slot)
    return widget


def combo(items):
    widget = QComboBox()
    widget.addItems(items)
    widget.setMinimumContentsLength(8)
    widget.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    return widget


def card(title):
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18,16,18,16)
    layout.setSpacing(12)
    layout.addWidget(label(title, "cardHeading"))
    return frame, layout


def history_status(entry):
    duration = float(entry.get("duration") or 0)
    current = float(entry.get("current_time") or 0)
    return "Completed" if duration > 0 and current / duration >= .9 else "Watched"


class HistoryPanel(QWidget):
    activated = Signal(object)
    clear_requested = Signal()

    def __init__(self, cover_path):
        super().__init__()
        self.cover_path = cover_path
        self.entries = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        layout.setSpacing(14)
        filters = QHBoxLayout()
        self.search = QLineEdit(); self.search.setPlaceholderText("Search history…"); self.search.setClearButtonEnabled(True)
        self.anime = combo(["All anime"])
        self.status = combo(["All status", "Watched", "Completed"])
        self.period = combo(["All time", "Last 7 days", "Last 30 days", "Last 90 days"])
        filters.addWidget(self.search, 2)
        for control in (self.anime, self.status, self.period): filters.addWidget(control, 1)
        layout.addLayout(filters)
        frame, body = card("Watch history")
        toolbar = QHBoxLayout()
        self.count = label("No history yet")
        toolbar.addWidget(self.count); toolbar.addStretch()
        self.order = combo(["Newest first", "Oldest first"])
        toolbar.addWidget(self.order)
        toolbar.addWidget(button("Clear history", self.clear_requested.emit))
        body.addLayout(toolbar)
        self.list = AnimeList(); self.list.setProperty("variant", "history")
        self.list.itemActivated.connect(lambda item: self.activated.emit(item.data(Qt.UserRole + 1)))
        body.addWidget(self.list, 1)
        layout.addWidget(frame, 1)
        self.search.textChanged.connect(self.refresh)
        for control in (self.anime, self.status, self.period, self.order): control.currentIndexChanged.connect(self.refresh)

    def set_entries(self, entries):
        self.entries = list(entries)
        selected = self.anime.currentText()
        self.anime.blockSignals(True)
        self.anime.clear(); self.anime.addItem("All anime")
        self.anime.addItems(sorted({e.get("title", "Unknown") for e in entries}))
        self.anime.setCurrentIndex(max(0, self.anime.findText(selected)))
        self.anime.blockSignals(False)
        self.refresh()

    def refresh(self, *_):
        view = self.list.view_state()
        self.list.clear()
        query = self.search.text().casefold().strip()
        days = (None,7,30,90)[self.period.currentIndex()]
        cutoff = time.time()-days*86400 if days else 0
        entries = [e for e in self.entries if query in f"{e.get('title','')} {e.get('website','')}".casefold()
                   and (self.anime.currentIndex() == 0 or e.get("title") == self.anime.currentText())
                   and (self.status.currentIndex() == 0 or history_status(e) == self.status.currentText())
                   and (not cutoff or (e.get("last_watched") or 0) >= cutoff)]
        entries.sort(key=lambda e:e.get("last_watched") or 0, reverse=self.order.currentIndex() == 0)
        last_day = None
        for entry in entries:
            stamp = entry.get("last_watched") or 0
            day = datetime.fromtimestamp(stamp).strftime("%b %d, %Y") if stamp else "Earlier history"
            if day != last_day:
                item = QListWidgetItem(day); item.setFlags(Qt.NoItemFlags)
                self.list.addItem(item); last_day = day
            e = dict(entry, cached_cover=str(self.cover_path(entry.get("cover") or "")))
            item = QListWidgetItem(e.get("title") or "Unknown")
            item.setData(Qt.UserRole + 1, e)
            item.setData(DETAIL_ROLE, f"Episode {e.get('episode') or '—'} · {history_status(e)}" +
                         (f" · {datetime.fromtimestamp(stamp):%b %d · %H:%M}" if stamp else ""))
            self.list.addItem(item)
        self.count.setText(f"{len(entries)} entries" if entries else "No matching history. Play an anime or adjust your filters.")
        self.list.restore_view(view)


class ActivityPanel(QWidget):
    clear_requested = Signal()
    PAGE_SIZE = 40

    def __init__(self, journal):
        super().__init__()
        self.journal = journal
        self.page = 0
        self.filtered = []
        layout = QVBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.setSpacing(14)
        filters = QGridLayout()
        self.filters = filters
        self.search = QLineEdit(); self.search.setPlaceholderText("Search activity…"); self.search.setClearButtonEnabled(True)
        self.kind = combo(["All types", "Playback", "Progress", "Account", "Connection", "Setting", "System"])
        self.period = combo(["All time", "Last 7 days", "Last 30 days"])
        self.filter_controls = [self.search,self.kind,self.period,button("Clear",self.clear_requested.emit),button("Export CSV",self.export)]
        for i,control in enumerate(self.filter_controls):filters.addWidget(control,0,i)
        layout.addLayout(filters)
        self.body = QHBoxLayout()
        frame, table_layout = card("Activity timeline")
        self.table = QTableWidget(0,5)
        self.table.setHorizontalHeaderLabels(["Time", "Type", "Anime", "Episode", "Status"])
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setItemDelegate(AnimeDelegate(self.table))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4,QHeaderView.Stretch)
        self.table.verticalHeader().setDefaultSectionSize(42)
        table_layout.addWidget(self.table, 1)
        footer = QHBoxLayout(); self.count = label("")
        self.previous = button("Previous", lambda:self.turn(-1)); self.next = button("Next", lambda:self.turn(1))
        footer.addWidget(self.count,1); footer.addWidget(self.previous); footer.addWidget(self.next)
        table_layout.addLayout(footer)
        self.details, detail_layout = card("Event details")
        self.details.setMinimumWidth(220); self.details.setMaximumWidth(290)
        self.detail_title = label("Select an event", "cardHeading")
        self.detail_body = label("Connection, playback and app events appear here.")
        self.detail_body.setTextFormat(Qt.PlainText)
        self.detail_body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        detail_layout.addWidget(self.detail_title); detail_layout.addWidget(self.detail_body); detail_layout.addStretch()
        self.body.addWidget(frame, 1); self.body.addWidget(self.details)
        layout.addLayout(self.body, 1)
        self.table.itemSelectionChanged.connect(self.show_details)
        self.search.textChanged.connect(self.reset)
        self.kind.currentIndexChanged.connect(self.reset)
        self.period.currentIndexChanged.connect(self.reset)
        self.refresh()

    def reset(self, *_): self.page = 0; self.refresh()
    def turn(self, amount): self.page += amount; self.refresh()

    def refresh(self):
        query = self.search.text().casefold().strip()
        days = (None,7,30)[self.period.currentIndex()]
        cutoff = time.time()-days*86400 if days else 0
        self.filtered = [e for e in reversed(self.journal.entries) if
            query in f"{e.get('message','')} {e.get('title','')} {e.get('type','')}".casefold()
            and (self.kind.currentIndex()==0 or e.get("type")==self.kind.currentText()) and e["time"]>=cutoff]
        self.page = min(max(0,self.page), max(0,(len(self.filtered)-1)//self.PAGE_SIZE))
        entries = self.filtered[self.page*self.PAGE_SIZE:(self.page+1)*self.PAGE_SIZE]
        self.table.setRowCount(len(entries))
        for row,e in enumerate(entries):
            values = [datetime.fromtimestamp(e["time"]).strftime("%b %d, %H:%M"), e.get("type","System"),
                      e.get("title") or "—", str(e.get("episode") or "—"), e.get("message","")]
            for col,value in enumerate(values):
                item = QTableWidgetItem(value); item.setToolTip(value); item.setData(Qt.UserRole,e)
                self.table.setItem(row,col,item)
        self.count.setText(f"{self.page*self.PAGE_SIZE+1 if entries else 0}–{self.page*self.PAGE_SIZE+len(entries)} of {len(self.filtered)} events")
        self.previous.setEnabled(self.page>0); self.next.setEnabled((self.page+1)*self.PAGE_SIZE<len(self.filtered))
        if entries: self.table.selectRow(0)
        else: self.detail_title.setText("No matching events"); self.detail_body.setText("Try another search or date range.")

    def show_details(self):
        row = self.table.currentRow()
        item = self.table.item(row,0)
        if item is None: return
        e = item.data(Qt.UserRole)
        self.detail_title.setText(e.get("title") or e.get("type","Event"))
        self.detail_body.setText(f"{datetime.fromtimestamp(e['time']):%b %d, %Y · %H:%M}\n\n{e.get('message','')}\n\n"
                                f"Type: {e.get('type','System')}\nEpisode: {e.get('episode') or '—'}\nAccount: {e.get('account') or 'Default'}")

    def export_to(self, path):
        with open(path,"w",encoding="utf-8-sig",newline="") as file:
            writer = csv.writer(file); writer.writerow(["Time","Type","Anime","Episode","Message","Account"])
            for e in self.filtered:
                values = [datetime.fromtimestamp(e["time"]).isoformat(),e.get("type",""),e.get("title",""),
                          e.get("episode",""),e.get("message",""),e.get("account","")]
                writer.writerow(["'"+str(v) if str(v).startswith(("=","+","-","@")) else v for v in values])

    def export(self):
        path,_ = QFileDialog.getSaveFileName(self,"Export activity","AniPresence-activity.csv","CSV files (*.csv)")
        if path:
            try: self.export_to(path)
            except OSError as exc: self.detail_body.setText(f"Could not export activity: {exc}")

    def resizeEvent(self,event):
        for control in self.filter_controls:self.filters.removeWidget(control)
        if self.width()<900:
            self.filters.addWidget(self.search,0,0,1,4)
            for i,control in enumerate(self.filter_controls[1:]):self.filters.addWidget(control,1,i)
        else:
            for i,control in enumerate(self.filter_controls):self.filters.addWidget(control,0,i)
        self.details.setVisible(self.width()>=950)
        self.table.setColumnHidden(3,self.width()<700)
        super().resizeEvent(event)


class MetricChart(QWidget):
    def __init__(self, mode):
        super().__init__(); self.mode = mode; self.values = []; self.setMinimumHeight(185)

    def set_values(self, values): self.values = values; self.update()

    def paintEvent(self,event):
        p=QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        window=self.window(); accent=QColor(window.cfg.data.accent)
        light=getattr(window,"_applied_theme","dark")=="light"
        text=QColor("#62597a" if light else "#aaa3c7")
        values=self.values
        if not values or not any(v for _,v in values):
            p.setPen(text); p.drawText(self.rect(),Qt.AlignCenter,"No recorded data for this period"); return
        if self.mode=="donut":
            total=sum(v for _,v in values); side=min(self.height()-20,self.width()*.48)
            rect=QRectF(8,(self.height()-side)/2,side,side); start=90*16
            for i,(name,value) in enumerate(values):
                color=QColor.fromHsv((accent.hue()+i*9)%360, max(60,accent.saturation()-i*18), min(255,200+i*7))
                p.setPen(QPen(color,24)); span=-round(value/total*360*16)
                p.drawArc(rect.adjusted(14,14,-14,-14),start,span); start+=span
                y=20+i*23
                p.setPen(Qt.NoPen); p.setBrush(color); p.drawRoundedRect(QRectF(side+24,y,9,9),2,2)
                p.setPen(text); p.drawText(QRectF(side+40,y-5,self.width()-side-42,22),Qt.AlignVCenter,f"{name}  {value/total:.0%}")
            p.setPen(text); p.drawText(rect,Qt.AlignCenter,"Genre share")
            return
        area=QRectF(36,10,self.width()-48,self.height()-42); maximum=max(1,max(v for _,v in values)*1.1)
        p.setFont(QFont(p.font().family(),9))
        for i in range(5):
            y=area.bottom()-area.height()*i/4
            p.setPen(QColor(130,120,170,38)); p.drawLine(QPointF(area.left(),y),QPointF(area.right(),y))
            p.setPen(text); p.drawText(QRectF(0,y-9,32,18),Qt.AlignRight,f"{maximum*i/4:.0f}h")
        step=area.width()/len(values); points=[]
        for i,(name,value) in enumerate(values):
            x=area.left()+step*(i+.5); y=area.bottom()-value/maximum*area.height(); points.append(QPointF(x,y))
            if self.mode=="bars":
                gradient=QLinearGradient(0,y,0,area.bottom()); gradient.setColorAt(0,accent.lighter(145)); gradient.setColorAt(1,accent.darker(135))
                p.setBrush(gradient); p.setPen(Qt.NoPen); p.drawRoundedRect(QRectF(x-step*.3,y,step*.6,area.bottom()-y),4,4)
            p.setPen(text); p.drawText(QRectF(x-step/2,area.bottom()+7,step,22),Qt.AlignCenter,name)
        if self.mode=="line" and points:
            path=QPainterPath(points[0])
            for point in points[1:]: path.lineTo(point)
            fill=QPainterPath(path); fill.lineTo(points[-1].x(),area.bottom()); fill.lineTo(points[0].x(),area.bottom()); fill.closeSubpath()
            color=QColor(accent); color.setAlpha(45); p.fillPath(fill,color)
            p.setPen(QPen(accent.lighter(130),3)); p.drawPath(path)
            p.setBrush(accent.lighter(155)); p.setPen(Qt.NoPen)
            for point in points: p.drawEllipse(point,4,4)


class AnalyticsPanel(QWidget):
    view_all = Signal()

    def __init__(self, controller, cover_path):
        super().__init__(); self.controller=controller; self.cover_path=cover_path
        layout=QVBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.setSpacing(16)
        toolbar=QHBoxLayout(); toolbar.addWidget(label("Your viewing, at a glance"),1)
        self.period=combo(["All time","Last 7 days","Last 30 days","Last 90 days"]); toolbar.addWidget(self.period)
        toolbar.addWidget(button("View all titles",self.view_all.emit)); layout.addLayout(toolbar)
        self.summary=QGridLayout(); self.metrics={}; self.metric_frames=[]
        for i,(key,title) in enumerate((("time","Total watch time"),("episodes","Episodes in history"),("anime","Anime watched"),("streak","Current streak"))):
            frame,body=card(title); value=label("—","metricValue"); body.insertWidget(0,value)
            self.metrics[key]=value; self.metric_frames.append(frame); self.summary.addWidget(frame,0,i)
        layout.addLayout(self.summary)
        self.charts=QGridLayout(); self.frames=[]
        for title,mode in (("Weekly watch time · Local playback","bars"),("Monthly watch time · Last six months","line"),("Genre distribution · Watch time","donut")):
            frame,body=card(title); chart=MetricChart(mode); body.addWidget(chart)
            self.frames.append(frame); setattr(self,mode,chart)
        top,body=card("Top anime · Most watched")
        self.top=AnimeList(); self.top.setProperty("variant","ranking"); self.top.setMinimumHeight(235)
        self.top.itemActivated.connect(lambda item: self.window()._resume_stats_item(item))
        body.addWidget(self.top); self.frames.append(top)
        for i,frame in enumerate(self.frames):self.charts.addWidget(frame,i//2,i%2)
        layout.addLayout(self.charts)
        self.note=label("Daily and monthly charts use local playback only. Imported totals are included in All time.")
        layout.addWidget(self.note)
        self.period.currentIndexChanged.connect(self.refresh)

    def refresh(self,*_):
        log=self.controller.watchlog; titles=log.top_titles(None)
        days=(None,7,30,90)[self.period.currentIndex()]
        cutoff=(date.today()-timedelta(days=days-1)).isoformat() if days else ""
        if days:
            titles=[dict(e,seconds=sum(v for d,v in (e.get("days") or {}).items() if d>=cutoff)) for e in titles]
            titles=[e for e in titles if e["seconds"]>0]
        titles.sort(key=lambda e:e.get("seconds",0),reverse=True)
        total=sum(e.get("seconds",0) for e in titles)
        recent=self.controller.recent.items()
        if days: recent=[e for e in recent if (e.get("last_watched") or 0)>=time.time()-days*86400]
        self.metrics["time"].setText(format_hours(total))
        self.metrics["anime"].setText(str(len(titles)))
        self.metrics["episodes"].setText(str(len({(e.get("title"),e.get("episode")) for e in recent if e.get("episode")})))
        self.metrics["streak"].setText(f"{log.current_streak()}d")
        self.metrics["episodes"].setToolTip("Unique episodes retained in local history; imported episode numbers are not counted as playback events.")
        self.bars.set_values([(datetime.fromisoformat(d).strftime("%a"),s/3600) for d,s in log.last_n_days(7)])
        monthly=defaultdict(float)
        for d,s in log.days().items(): monthly[d[:7]]+=s/3600
        months=[]; year,month=date.today().year,date.today().month
        for offset in range(5,-1,-1):
            absolute=year*12+month-1-offset; y,m=divmod(absolute,12)
            key=f"{y:04}-{m+1:02}"; months.append((date(y,m+1,1).strftime("%b"),monthly[key]))
        self.line.set_values(months)
        genres_by_title={e.get("title"):e.get("genres") or [] for entries in log.anilist_sections().values() for e in entries}
        genres=defaultdict(float)
        for e in titles:
            tags=genres_by_title.get(e["title"]) or ["Unknown"]
            for tag in tags:genres[tag]+=e.get("seconds",0)/len(tags)
        ordered=sorted(genres.items(),key=lambda pair:pair[1],reverse=True)
        if len(ordered)>6:ordered=ordered[:6]+[("Other",sum(v for _,v in ordered[6:]))]
        self.donut.set_values(ordered)
        self.top.clear()
        for i,e in enumerate(titles[:5]):
            entry=dict(e,rank=i+1,share=(e.get("seconds",0)/total if total else 0),cached_cover=str(self.cover_path(e.get("cover") or "")))
            item=QListWidgetItem(e["title"]); item.setData(Qt.UserRole,e.get("url") or ""); item.setData(Qt.UserRole+1,entry)
            item.setData(DETAIL_ROLE,f"{format_hours(e.get('seconds',0))} watched · {entry['share']:.1%}"); self.top.addItem(item)

    def resizeEvent(self,event):
        columns=1 if self.width()<800 else 2
        for frame in self.frames:self.charts.removeWidget(frame)
        for i,frame in enumerate(self.frames):self.charts.addWidget(frame,i//columns,i%columns)
        for i in range(2):self.charts.setColumnStretch(i,1 if i<columns else 0)
        metric_columns=2 if self.width()<760 else 4
        for frame in self.metric_frames:self.summary.removeWidget(frame)
        for i,frame in enumerate(self.metric_frames):self.summary.addWidget(frame,i//metric_columns,i%metric_columns)
        for i in range(4):self.summary.setColumnStretch(i,1 if i<metric_columns else 0)
        super().resizeEvent(event)
