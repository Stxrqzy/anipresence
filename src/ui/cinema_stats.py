"""V3 watch-time dashboard, using reconciled dated records rather than sample data."""
from collections import Counter, defaultdict
from datetime import date, timedelta
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QFont
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QVBoxLayout
from .cinema import Page, action, clear
from .details import text, Artwork
from ..core.watchlog import format_hours


def period_data(snapshot, days):
    today = date.today()
    start = today-timedelta(days=days-1)
    cutoff = start.isoformat()
    times = {d:s for d,s in snapshot["days"].items() if cutoff <= d <= today.isoformat()}
    episodes = Counter(d for d in snapshot["episodes"].values() if cutoff <= d <= today.isoformat())
    dates = [start+timedelta(days=i) for i in range(days)]
    series = [(d.isoformat(), times.get(d.isoformat(),0)/3600, episodes[d.isoformat()]) for d in dates]
    if days == 365:
        groups = []
        for i in range(0, len(series), 7):
            group = series[i:i+7]
            groups.append((group[0][0],sum(v[1] for v in group),sum(v[2] for v in group)))
        series = groups
    titles = [dict(e, seconds=sum(v for d,v in e.get("days",{}).items() if cutoff <= d <= today.isoformat()))
              for e in snapshot["titles"]]
    titles = sorted((e for e in titles if e["seconds"]>0),key=lambda e:e["seconds"],reverse=True)
    best = run = 0
    for d in dates:
        run = run+1 if times.get(d.isoformat(),0)>0 else 0
        best = max(best,run)
    return {"series":series,"times":times,"episodes":sum(episodes.values()),"titles":titles,
            "total":sum(times.values()),"streak":best}


class Timeline(QFrame):
    def __init__(self):
        super().__init__(); self.setObjectName("card"); self.setMinimumHeight(440)
        self.series = []; self.year = False
        self.setMouseTracking(True)

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        ink = QColor("#777777")
        area = QRectF(50, 68, max(1,self.width()-105), self.height()-120)
        p.setPen(self.palette().windowText().color())
        p.drawText(24,32,"Hours per week" if self.year else "Episodes per day · Watch hours")
        if not self.series: return
        max_h = max(1, max(v[1] for v in self.series)*1.15)
        max_e = max(1, max(v[2] for v in self.series)*1.15)
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
                p.drawRoundedRect(QRectF(x-step*.25,area.bottom()-height,max(2,step*.5),height),4,4)
            points.append(QPointF(x,area.bottom()-hours/max_h*area.height()))
            if i in {round(j*(len(self.series)-1)/4) for j in range(5)}:
                p.setPen(ink);p.drawText(QRectF(x-35,area.bottom()+14,70,24),Qt.AlignCenter,date.fromisoformat(day).strftime("%b %d"))
        path=QPainterPath(points[0])
        # A monotone straight line keeps sparse measured totals honest.
        for point in points[1:]: path.lineTo(point)
        fill=QPainterPath(path);fill.lineTo(points[-1].x(),area.bottom());fill.lineTo(points[0].x(),area.bottom());fill.closeSubpath()
        p.fillPath(fill,QColor(177,156,255,40));p.setBrush(Qt.NoBrush);p.setPen(QPen(QColor("#b5a0ff"),2.5));p.drawPath(path)
        if not any(v[1] or v[2] for v in self.series):
            p.setPen(ink);p.drawText(area,Qt.AlignCenter,"No dated viewing activity in this range")

    def mouseMoveEvent(self,event):
        if self.series:
            i=max(0,min(len(self.series)-1,int((event.position().x()-50)/max(1,self.width()-105)*len(self.series))))
            day,hours,episodes=self.series[i]
            self.setToolTip(f"{day} · {format_hours(hours*3600)} · {episodes} unique episodes")
        super().mouseMoveEvent(event)


class Distribution(QFrame):
    def __init__(self, rings=False):
        super().__init__();self.setObjectName("card");self.setMinimumHeight(290)
        self.rings=rings;self.values=[]

    def paintEvent(self,event):
        super().paintEvent(event)
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        p.setPen(self.palette().windowText().color())
        p.drawText(24,32,"On your list · All time" if self.rings else "When you watch · Dated activity")
        colors=["#b6a0ff","#65d9f5","#ffd568","#999999","#f88798"]
        total=sum(v for _,v in self.values)
        if self.rings:
            side=min(190,self.width()*.43);rect=QRectF(28,65,side,side)
            for i,(name,value) in enumerate(self.values):
                ring=rect.adjusted(i*12,i*12,-i*12,-i*12)
                p.setPen(QPen(QColor("#303030"),9));p.drawEllipse(ring)
                p.setPen(QPen(QColor(colors[i%len(colors)]),9,Qt.SolidLine,Qt.RoundCap))
                p.drawArc(ring,90*16,-round(360*16*value/max(1,total)))
                p.setPen(QColor("#aaaaaa"));p.drawText(QRectF(side+48,74+i*33,self.width()-side-60,28),f"{name}   {value}")
            p.setPen(self.palette().windowText().color());p.drawText(rect,Qt.AlignCenter,str(total))
        else:
            area=QRectF(28,64,self.width()-56,self.height()-106);step=area.width()/7
            maximum=max(1,max((v for _,v in self.values),default=0))
            for i,(name,value) in enumerate(self.values):
                height=value/maximum*area.height()
                p.setBrush(QColor(colors[0]));p.setPen(Qt.NoPen)
                p.drawRoundedRect(QRectF(area.left()+i*step+8,area.bottom()-height,step-16,height),6,6)
                p.setPen(QColor("#999999"));p.drawText(QRectF(area.left()+i*step,area.bottom()+10,step,24),Qt.AlignCenter,name)


class Heatmap(QFrame):
    def __init__(self):
        super().__init__();self.setObjectName("card");self.setMinimumHeight(200);self.times={};self.days=90

    def paintEvent(self,event):
        super().paintEvent(event)
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        p.setPen(self.palette().windowText().color());p.drawText(24,30,"Viewing activity")
        start=date.today()-timedelta(days=self.days-1)
        start-=timedelta(days=start.weekday())
        weeks=(date.today()-start).days//7+1
        cell=min(18,max(4,(self.width()-70)//weeks-3))
        maximum=max(1,max(self.times.values(),default=0))
        last_month=None
        for i in range((date.today()-start).days+1):
            day=start+timedelta(days=i);x=28+(i//7)*(cell+3);y=64+(i%7)*(cell+3)
            value=self.times.get(day.isoformat(),0)
            color=QColor("#262626")
            if value:color=QColor.fromRgbF(.34+value/maximum*.38,.27+value/maximum*.35,.5+value/maximum*.5)
            p.setPen(Qt.NoPen);p.setBrush(color);p.drawRoundedRect(QRectF(x,y,cell,cell),2,2)
            if day.month!=last_month and day.weekday()==0:
                p.setPen(QColor("#aaaaaa"));p.drawText(x,54,day.strftime("%b"));last_month=day.month
        self.setMinimumHeight(80+7*(cell+3))


class Statistics(Page):
    def __init__(self):
        super().__init__();self.days=30
        self.body.addWidget(text("Watch time","cinemaHeading"))
        row=QGridLayout();self.toolbar=row;self.periods={}
        for days,caption in ((30,"Last 30 days"),(90,"Last 90 days"),(365,"Last year")):
            b=action(caption,lambda checked=False,d=days:self.select(d));b.setCheckable(True);b.setProperty("cinemaPill",True)
            self.periods[days]=b;row.addWidget(b,0,len(self.periods)-1)
        self.sync=action("Refresh AniList",lambda:self.window()._analytics.sync_anilist())
        row.setColumnStretch(3,1);row.addWidget(self.sync,0,4)
        self.body.addLayout(row)
        self.metrics=QGridLayout();self.frames=[];self.values={}
        for key,caption in (("time","watch hours"),("episodes","unique dated episodes"),("series","series"),("finished","finished · local"),
                            ("active","active days"),("streak","best streak"),("completion","completion · local")):
            frame=QFrame();frame.setObjectName("card");box=QVBoxLayout(frame);box.setContentsMargins(24,22,24,22)
            value=text("—","cinemaHeading");box.addWidget(value);box.addWidget(text(caption,"cinemaMuted"))
            self.frames.append(frame);self.values[key]=value
        self.body.addLayout(self.metrics)
        self.chart=Timeline();self.body.addWidget(self.chart)
        self.heatmap=Heatmap();self.body.addWidget(self.heatmap)
        self.distributions=QGridLayout()
        self.week=Distribution();self.rings=Distribution(True)
        self.distributions.addWidget(self.week,0,0);self.distributions.addWidget(self.rings,0,1);self.body.addLayout(self.distributions)
        self.body.addWidget(text("Most watched","cinemaSection"));self.top=QVBoxLayout();self.body.addLayout(self.top)
        self.note=text("Measured playback plus reconciled AniList estimates. Feed dates are list updates; undated list progress is excluded from these ranges.","cinemaMuted")
        self.body.addWidget(self.note);self.body.addStretch()

    def select(self,days):
        self.days=days;self.refresh()

    def refresh(self):
        w=self.window();data=period_data(w.controller.watchlog.analytics(),self.days)
        for d,b in self.periods.items():b.setChecked(d==self.days)
        for key,value in (("time",format_hours(data["total"])),("episodes",data["episodes"]),
                          ("series",len(data["titles"])),("active",sum(v>0 for v in data["times"].values())),("streak",f"{data['streak']}d")):
            self.values[key].setText(str(value))
        self.chart.series=data["series"];self.chart.year=self.days==365;self.chart.update()
        self.heatmap.times=data["times"];self.heatmap.days=self.days;self.heatmap.update()
        cutoff=(date.today()-timedelta(days=self.days-1)).isoformat()
        from datetime import datetime
        measured=[e for e in w.controller.watchlog.history(w.controller.recent.items(),copy_result=False)
                  if e.get("duration") and e.get("current_time") is not None and
                  datetime.fromtimestamp(e.get("last_watched") or 0).date().isoformat()>=cutoff]
        fractions=[min(1,max(0,e["current_time"]/e["duration"])) for e in measured]
        self.values["finished"].setText(str(sum(f>=.9 for f in fractions)))
        self.values["completion"].setText(f"{sum(fractions)/len(fractions):.0%}" if fractions else "—")
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
        cols=2 if self.viewport().width()<850 else 4 if self.viewport().width()<1250 else 7
        narrow=self.viewport().width()<850
        self.toolbar.addWidget(self.sync,1 if narrow else 0,0 if narrow else 4)
        for i,frame in enumerate(self.frames):self.metrics.addWidget(frame,i//cols,i%cols)
        for i in range(7):self.metrics.setColumnStretch(i,1 if i<cols else 0)
        self.distributions.addWidget(self.rings,1 if self.viewport().width()<850 else 0,0 if self.viewport().width()<850 else 1)

    def resizeEvent(self,event):
        self.reflow()
        self.body.activate()
        super().resizeEvent(event)
