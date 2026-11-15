"""V3 watch-time dashboard, using reconciled dated records rather than sample data."""
from collections import Counter, defaultdict
import math
from datetime import date, timedelta
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QFont
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QVBoxLayout, QToolTip
from .cinema import Page, action, clear
from .details import text, Artwork
from ..core.watchlog import format_hours


def period_data(snapshot, days, end=None):
    """All-time totals retain undated list progress; charts only use dated records."""
    today = end or date.today()
    all_time = days is None
    valid = [d for d in snapshot["days"] if d and d <= today.isoformat()]
    valid.extend(d for d in snapshot["episodes"].values() if d and d <= today.isoformat())
    start = date.fromisoformat(min(valid)) if all_time and valid else today
    if not all_time: start = today-timedelta(days=days-1)
    count = (today-start).days+1
    cutoff = start.isoformat()
    times = {d:s for d,s in snapshot["days"].items() if cutoff <= d <= today.isoformat()}
    episodes = Counter(d for d in snapshot["episodes"].values() if cutoff <= d <= today.isoformat())
    dates = [start+timedelta(days=i) for i in range(count)]
    series = [(d.isoformat(), times.get(d.isoformat(),0)/3600, episodes[d.isoformat()]) for d in dates]
    bucket = "day"
    if all_time and count > 366:
        monthly = {}
        for day,hours,eps in series:
            key = day[:7]+"-01"
            old = monthly.get(key,(0,0))
            monthly[key] = (old[0]+hours,old[1]+eps)
        series = [(d,h,e) for d,(h,e) in monthly.items()]
        bucket = "month"
    elif days == 365 or (all_time and count > 90):
        groups = []
        for i in range(0, len(series), 7):
            group = series[i:i+7]
            groups.append((group[0][0],sum(v[1] for v in group),sum(v[2] for v in group)))
        series = groups
        bucket = "week"
    titles = [dict(e, seconds=e.get("seconds",0) if all_time else
                   sum(v for d,v in e.get("days",{}).items() if cutoff <= d <= today.isoformat()))
              for e in snapshot["titles"]]
    titles = sorted((e for e in titles if e["seconds"]>0),key=lambda e:e["seconds"],reverse=True)
    best = run = 0
    for d in dates:
        run = run+1 if times.get(d.isoformat(),0)>0 else 0
        best = max(best,run)
    total = sum(e["seconds"] for e in titles) if all_time else sum(times.values())
    estimated = sum(e.get("estimated_seconds",0) if all_time else
                    sum(v for d,v in e.get("estimated_days",{}).items() if cutoff <= d <= today.isoformat())
                    for e in titles)
    estimated = min(total,max(0,estimated))
    return {"series":series,"times":times,
            "episodes":snapshot.get("unique_episode_total",sum(episodes.values())) if all_time else sum(episodes.values()),
            "titles":titles,"total":total,"streak":best,"measured":max(0,total-estimated),"estimated":estimated,
            "undated":max(0,total-sum(times.values())) if all_time else 0,
            "bucket":bucket,"span":count,"cutoff":cutoff}


def comparison_text(current, previous):
    if not previous:
        return "No activity in the previous period" if not current else "New activity · previous period had none"
    change = (current-previous)/previous
    return f"{change:+.0%} watch time vs previous period"


class Timeline(QFrame):
    def __init__(self):
        super().__init__(); self.setObjectName("card"); self.setMinimumHeight(440)
        self.series = []; self.year = False; self.bucket = "day"; self.hover = -1
        self.setMouseTracking(True)

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        ink = QColor("#777777")
        area = QRectF(50, 68, max(1,self.width()-105), self.height()-120)
        p.setPen(self.palette().windowText().color())
        p.drawText(24,32,f"Watch hours per {self.bucket}" if self.year else "Episodes per day · Watch hours")
        if not self.series: return
        max_h = max(1, max(v[1] for v in self.series)*1.15)
        max_e = max(4, math.ceil(max(v[2] for v in self.series)*1.15/4)*4)
        for i in range(5):
            y=area.bottom()-area.height()*i/4
            p.setPen(QPen(QColor(128,128,128,38),1,Qt.DashLine))
            p.drawLine(QPointF(area.left(),y),QPointF(area.right(),y)); p.setPen(ink)
            p.drawText(QRectF(3,y-10,40,20),Qt.AlignRight,f"{max_h*i/4:.1f}h" if self.year else f"{max_e*i/4:.0f}")
            if not self.year:p.drawText(QRectF(area.right()+8,y-10,44,20),f"{max_h*i/4:.1f}h")
        step=area.width()/max(1,len(self.series)); points=[]
        for i,(day,hours,episodes) in enumerate(self.series):
            x=area.left()+(i+.5)*step
            if not self.year:
                height=episodes/max_e*area.height()
                p.setBrush(QColor("#65d9f5"));p.setPen(Qt.NoPen)
                bar_width=max(2,min(24,step*.5))
                p.drawRoundedRect(QRectF(x-bar_width/2,area.bottom()-height,bar_width,height),4,4)
            points.append(QPointF(x,area.bottom()-hours/max_h*area.height()))
            if i in {round(j*(len(self.series)-1)/4) for j in range(5)}:
                p.setPen(ink);p.drawText(QRectF(x-42,area.bottom()+14,84,24),Qt.AlignCenter,
                                       date.fromisoformat(day).strftime("%b %Y" if self.bucket=="month" else "%b %d"))
        path=QPainterPath(points[0])
        # A monotone straight line keeps sparse measured totals honest.
        for point in points[1:]: path.lineTo(point)
        fill=QPainterPath(path);fill.lineTo(points[-1].x(),area.bottom());fill.lineTo(points[0].x(),area.bottom());fill.closeSubpath()
        p.fillPath(fill,QColor(177,156,255,40));p.setBrush(Qt.NoBrush);p.setPen(QPen(QColor("#b5a0ff"),2.5));p.drawPath(path)
        if len(points)==1:
            p.setBrush(QColor("#b5a0ff"));p.setPen(Qt.NoPen);p.drawEllipse(points[0],4,4)
        if 0 <= self.hover < len(points):
            point = points[self.hover]
            p.setPen(QPen(QColor("#aaaaaa"),1,Qt.DashLine))
            p.drawLine(QPointF(point.x(),area.top()),QPointF(point.x(),area.bottom()))
            p.setBrush(QColor("#b5a0ff"));p.setPen(Qt.NoPen);p.drawEllipse(point,4,4)
        if not any(v[1] or v[2] for v in self.series):
            p.setPen(ink);p.drawText(area,Qt.AlignCenter,"No dated viewing activity in this range")

    def mouseMoveEvent(self,event):
        if self.series:
            i=max(0,min(len(self.series)-1,int((event.position().x()-50)/max(1,self.width()-105)*len(self.series))))
            day,hours,episodes=self.series[i]
            self.hover=i;self.update()
            prefix = f"{self.bucket.title()} starting " if self.bucket!="day" else ""
            QToolTip.showText(event.globalPosition().toPoint(),f"{prefix}{day}\n{format_hours(hours*3600)} · {episodes} unique dated episodes",self)
        super().mouseMoveEvent(event)

    def leaveEvent(self,event):
        self.hover=-1;self.update();QToolTip.hideText();super().leaveEvent(event)


class Distribution(QFrame):
    def __init__(self, rings=False):
        super().__init__();self.setObjectName("card");self.setMinimumHeight(290)
        self.rings=rings;self.values=[]
        self.setMouseTracking(True)

    def mouseMoveEvent(self,event):
        if self.values:
            index = int((event.position().y()-74)/33) if self.rings else int((event.position().x()-28)/max(1,self.width()-56)*7)
            if 0 <= index < len(self.values):
                name,value=self.values[index]
                detail=f"{value} titles" if self.rings else format_hours(value)
                QToolTip.showText(event.globalPosition().toPoint(),f"{name}: {detail}",self)
        super().mouseMoveEvent(event)

    def paintEvent(self,event):
        super().paintEvent(event)
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        light=self.window()._applied_theme=="light"
        muted=QColor("#626262" if light else "#aaaaaa")
        p.setPen(self.palette().windowText().color())
        p.drawText(24,32,"On your list · All time" if self.rings else "When you watch · Dated activity")
        colors=["#b6a0ff","#65d9f5","#ffd568","#999999","#f88798"]
        total=sum(v for _,v in self.values)
        if self.rings:
            side=min(190,self.width()*.43);rect=QRectF(28,65,side,side)
            for i,(name,value) in enumerate(self.values):
                ring=rect.adjusted(i*12,i*12,-i*12,-i*12)
                p.setPen(QPen(QColor("#d1d1d1" if light else "#303030"),9));p.drawEllipse(ring)
                p.setPen(QPen(QColor(colors[i%len(colors)]),9,Qt.SolidLine,Qt.RoundCap))
                p.drawArc(ring,90*16,-round(360*16*value/max(1,total)))
                p.setPen(muted);p.drawText(QRectF(side+48,74+i*33,self.width()-side-60,28),f"{name}   {value}")
            p.setPen(self.palette().windowText().color());p.drawText(rect,Qt.AlignCenter,str(total))
        else:
            area=QRectF(28,64,self.width()-56,self.height()-106);step=area.width()/7
            maximum=max(1,max((v for _,v in self.values),default=0))
            for i,(name,value) in enumerate(self.values):
                height=value/maximum*area.height()
                p.setBrush(QColor(colors[0]));p.setPen(Qt.NoPen)
                p.drawRoundedRect(QRectF(area.left()+i*step+8,area.bottom()-height,step-16,height),6,6)
                p.setPen(muted);p.drawText(QRectF(area.left()+i*step,area.bottom()+10,step,24),Qt.AlignCenter,name)


class Heatmap(QFrame):
    def __init__(self):
        super().__init__();self.setObjectName("card");self.setMinimumHeight(200);self.times={};self.days=90
        self.cells=[];self.all_time=False;self.setMouseTracking(True)

    def paintEvent(self,event):
        super().paintEvent(event)
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        light=self.window()._applied_theme=="light"
        muted=QColor("#626262" if light else "#aaaaaa")
        p.setPen(self.palette().windowText().color());p.drawText(24,30,"Viewing activity · Last 365 days" if self.all_time else "Viewing activity")
        start=date.today()-timedelta(days=self.days-1)
        start-=timedelta(days=start.weekday())
        weeks=(date.today()-start).days//7+1
        step=min(21,(self.width()-56)/weeks);cell=max(2,step-3)
        maximum=max(1,max(self.times.values(),default=0))
        last_month=None
        self.cells=[]
        for i in range((date.today()-start).days+1):
            day=start+timedelta(days=i);x=28+(i//7)*step;y=64+(i%7)*(cell+3)
            value=self.times.get(day.isoformat(),0)
            color=QColor("#d9d9d9" if light else "#262626")
            if value:
                ratio=value/maximum
                color=QColor.fromRgbF(.72-ratio*.35,.65-ratio*.4,.95-ratio*.2) if light else QColor.fromRgbF(.34+ratio*.38,.27+ratio*.35,.5+ratio*.5)
            rect=QRectF(x,y,cell,cell);self.cells.append((rect,day.isoformat(),value))
            p.setPen(Qt.NoPen);p.setBrush(color);p.drawRoundedRect(rect,2,2)
            if day.month!=last_month and day.weekday()==0:
                p.setPen(muted);p.drawText(x,54,day.strftime("%b"));last_month=day.month
        p.setPen(muted);p.drawText(28,int(90+7*(cell+3)),"Grey: no activity   ·   Purple: watch time")

    def mouseMoveEvent(self,event):
        for rect,day,value in self.cells:
            if rect.contains(event.position()):
                QToolTip.showText(event.globalPosition().toPoint(),f"{day} · {format_hours(value)}",self)
                break
        super().mouseMoveEvent(event)


class Statistics(Page):
    def __init__(self):
        super().__init__();self.days=30
        self.body.addWidget(text("Watch time","cinemaHeading"))
        row=QGridLayout();self.toolbar=row;self.periods={}
        for days,caption in ((30,"Last 30 days"),(90,"Last 90 days"),(365,"Last year"),(None,"All time")):
            b=action(caption,lambda checked=False,d=days:self.select(d));b.setCheckable(True);b.setProperty("cinemaPill",True)
            self.periods[days]=b;row.addWidget(b,0,len(self.periods)-1)
        self.sync=action("Refresh AniList",lambda:self.window()._analytics.sync_anilist())
        row.addWidget(self.sync,0,4)
        self.body.addLayout(row)
        self.comparison=text("","cinemaMuted");self.body.addWidget(self.comparison)
        self.sources=text("","cinemaMuted");self.body.addWidget(self.sources)
        self.metrics=QGridLayout();self.frames=[];self.values={}
        for key,caption in (("time","watch hours"),("episodes","unique dated episodes"),("series","series"),("finished","finished · local"),
                            ("active","active days"),("streak","best streak"),("completion","completion · local")):
            frame=QFrame();frame.setObjectName("card");box=QVBoxLayout(frame);box.setContentsMargins(24,22,24,22)
            value=text("—","cinemaHeading");box.addWidget(value);box.addWidget(text(caption,"cinemaMuted"))
            self.frames.append(frame);self.values[key]=value
            if key=="episodes": self.episode_caption=box.itemAt(1).widget()
        self.body.addLayout(self.metrics)
        self.chart=Timeline();self.body.addWidget(self.chart)
        self.legend=text("","cinemaMuted");self.body.addWidget(self.legend)
        self.heatmap=Heatmap();self.body.addWidget(self.heatmap)
        self.heatmap.setMinimumHeight(270)
        self.distributions=QGridLayout()
        self.week=Distribution();self.rings=Distribution(True)
        self.distributions.addWidget(self.week,0,0);self.distributions.addWidget(self.rings,0,1);self.body.addLayout(self.distributions)
        self.body.addWidget(text("Most watched","cinemaSection"));self.top=QVBoxLayout();self.body.addLayout(self.top)
        self.note=text("Measured playback plus reconciled AniList estimates. Feed dates are list updates; undated list progress is excluded from these ranges.","cinemaMuted")
        self.body.addWidget(self.note);self.body.addStretch()

    def select(self,days):
        self.days=days;self.refresh();self.verticalScrollBar().setValue(0)

    def refresh(self):
        w=self.window();snapshot=w.controller.watchlog.analytics();data=period_data(snapshot,self.days)
        self.episode_caption.setText("unique episodes · all time" if self.days is None else "unique dated episodes")
        if self.days is None:
            self.comparison.setText("All recorded time, including reconciled AniList list progress")
        else:
            previous=period_data(snapshot,self.days,date.today()-timedelta(days=self.days))
            self.comparison.setText(comparison_text(data["total"],previous["total"]))
        for d,b in self.periods.items():b.setChecked(d==self.days)
        for key,value in (("time",format_hours(data["total"])),("episodes",data["episodes"]),
                          ("series",len(data["titles"])),("active",sum(v>0 for v in data["times"].values())),("streak",f"{data['streak']}d")):
            self.values[key].setText(str(value))
        self.chart.series=data["series"];self.chart.bucket=data["bucket"];self.chart.year=data["bucket"]!="day";self.chart.hover=-1;self.chart.update()
        self.legend.setText(("Purple line: watch hours" if self.chart.year else "Cyan bars: unique dated episodes · Purple line: watch hours")+
                            "\nHover over the chart, activity cells or weekday bars for values.")
        self.heatmap.times=data["times"];self.heatmap.days=self.days or 365;self.heatmap.all_time=self.days is None;self.heatmap.update()
        cutoff=data["cutoff"] if self.days else ""
        from datetime import datetime
        measured=[e for e in w.controller.watchlog.history(w.controller.recent.items(),copy_result=False)
                  if e.get("duration") and e.get("current_time") is not None and
                  datetime.fromtimestamp(e.get("last_watched") or 0).date().isoformat()>=cutoff]
        fractions=[min(1,max(0,e["current_time"]/e["duration"])) for e in measured]
        self.values["finished"].setText(str(sum(f>=.9 for f in fractions)))
        self.values["completion"].setText(f"{sum(fractions)/len(fractions):.0%}" if fractions else "—")
        self.sources.setText(f"Measured: {format_hours(data['measured'])}   ·   Estimated: {format_hours(data['estimated'])}"+
                             (f"   ·   Undated: {format_hours(data['undated'])} (included in totals)" if self.days is None else ""))
        self.note.setText(f"Measured playback: {format_hours(data['measured'])} · Estimated: {format_hours(data['estimated'])}"+
                          (f" · Undated total: {format_hours(data['undated'])}" if self.days is None else "")+
                          "\nEstimates use AniList progress/feed data and exclude overlapping measured playback. "
                          "Charts use dated activity only; feed dates are list updates. Local completion uses saved playback positions.")
        week=defaultdict(float)
        for d,s in data["times"].items():week[date.fromisoformat(d).weekday()]+=s
        self.week.values=[(name,week[i]) for i,name in enumerate(("Mon","Tue","Wed","Thu","Fri","Sat","Sun"))];self.week.update()
        self.rings.values=[(name,len(rows)) for name,rows in w.controller.watchlog.anilist_sections().items() if rows][:5];self.rings.update()
        clear(self.top)
        for i,e in enumerate(data["titles"][:12]):
            frame=QFrame();frame.setObjectName("card");row=QHBoxLayout(frame)
            row.addWidget(text(str(i+1),"cinemaMuted"))
            art=Artwork();art.url=e.get("cover") or "";art.setFixedSize(48,70);row.addWidget(art)
            info=QVBoxLayout();info.addWidget(text(e["title"],"cinemaSection"));info.addWidget(text(format_hours(e["seconds"]),"cinemaMuted"));row.addLayout(info,1)
            row.addWidget(text(f"{e['seconds']/max(1,data['total']):.0%}","cinemaMuted"))
            row.addWidget(action("Details",lambda checked=False,entry=e:w._open_anime(entry),"ghost"));self.top.addWidget(frame)
        if not data["titles"]:self.top.addWidget(text("No titles with dated watch time in this period.","cinemaMuted"))
        self.reflow()

    def reflow(self):
        width=self.viewport().width()
        cols=2 if width<850 else 4 if width<1250 else 7
        narrow=width<1000
        for i,b in enumerate(self.periods.values()):self.toolbar.addWidget(b,i//2 if narrow else 0,i%2 if narrow else i)
        self.toolbar.addWidget(self.sync,2 if narrow else 0,0 if narrow else 4)
        for i,frame in enumerate(self.frames):self.metrics.addWidget(frame,i//cols,i%cols)
        for i in range(7):self.metrics.setColumnStretch(i,1 if i<cols else 0)
        self.distributions.addWidget(self.rings,1 if self.viewport().width()<850 else 0,0 if self.viewport().width()<850 else 1)

    def resizeEvent(self,event):
        self.reflow()
        self.body.activate()
        super().resizeEvent(event)
