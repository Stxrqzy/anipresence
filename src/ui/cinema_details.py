"""V3 cinematic details: persistent facts rail, episode tiles and catalogue tabs."""
import webbrowser
import time
import math
from datetime import datetime
from PySide6.QtCore import Qt, QRectF, QTimer
from PySide6.QtGui import QColor, QPainter, QLinearGradient
from PySide6.QtWidgets import (QApplication, QFrame, QGridLayout, QHBoxLayout, QLineEdit,
                              QListWidgetItem, QStackedWidget, QVBoxLayout, QWidget, QSizePolicy)
from .details import DetailsPage, Artwork, text
from .cinema import action, clear
from .library import PosterGrid
from .poster import draw_filled_pixmap
from .playback import playback_state, resume_entry


class Scene(QFrame):
    def __init__(self):
        super().__init__();self.url="";self.setObjectName("surfaceContent")

    def paintEvent(self,event):
        p=QPainter(self);w=self.window()
        base=QColor("#f5f5f5" if w._applied_theme=="light" else "#080808")
        p.fillRect(self.rect(),base)
        if self.url:
            pm=w._images.get(w._cover_cache_path(self.url),1800,1000)
            if pm.isNull():w._request_library_poster(self.url)
            else:draw_filled_pixmap(p,QRectF(0,0,self.width(),650),pm,0)
        veil=QLinearGradient(0,0,0,700)
        base.setAlpha(100);veil.setColorAt(0,base)
        base.setAlpha(180);veil.setColorAt(.6,base)
        base.setAlpha(255);veil.setColorAt(1,base)
        p.fillRect(QRectF(0,0,self.width(),700),veil)


class CinemaDetails(DetailsPage):
    def __init__(self):
        super().__init__()
        old=self.takeWidget()
        host=Scene()
        for widget in (self.notice,self.retry,self.poster,self.logo,self.title,self.metadata,self.synopsis,self.expand,
                       self.watch,self.ranges,self.episode_note):
            widget.setParent(host)
        self.setWidget(host);self.hero=host
        self.body=QVBoxLayout(host);self.body.setContentsMargins(30,24,30,30);self.body.setSpacing(24)
        top=QHBoxLayout();top.addWidget(action("← Back",self.go_back,"ghost"));top.addWidget(self.notice,1);top.addWidget(self.retry)
        self.body.addLayout(top)
        self.split=QGridLayout();self.split.setHorizontalSpacing(36);self.body.addLayout(self.split)
        self.rail=QWidget();self.rail.setObjectName("surfaceContent");left=QVBoxLayout(self.rail);left.setContentsMargins(0,0,0,0);left.setSpacing(20)
        self.rail.setFixedWidth(260)
        self.poster.setFixedSize(260,390);left.addWidget(self.poster)
        self.facts=text("","cinemaMuted");self.facts.setTextInteractionFlags(Qt.TextSelectableByMouse);left.addWidget(self.facts);left.addStretch()
        self.right=QWidget();self.right.setObjectName("surfaceContent");body=QVBoxLayout(self.right);body.setContentsMargins(0,0,0,0);body.setSpacing(20)
        self.logo.setFixedHeight(135);self.logo.setMaximumWidth(460);self.title.setObjectName("cinemaHeroTitle")
        self.metadata.setObjectName("cinemaMeta")
        for widget in (self.logo,self.title,self.metadata,self.synopsis,self.expand):body.addWidget(widget)
        self.synopsis.setMaximumWidth(850);self.expand.setMaximumWidth(80)
        self.airing=text("","cinemaMeta");body.addWidget(self.airing)
        self.airing_timer=QTimer(self);self.airing_timer.setInterval(60000);self.airing_timer.timeout.connect(self.refresh_airing)
        self.visible_artwork_timer=QTimer(self);self.visible_artwork_timer.setSingleShot(True);self.visible_artwork_timer.setInterval(60)
        self.visible_artwork_timer.timeout.connect(self.refresh_artwork)
        self.verticalScrollBar().valueChanged.connect(lambda _:self.visible_artwork_timer.start())
        self.buttons=QGridLayout()
        self.watch.setObjectName("cinemaPrimary")
        self.trailer=action("Trailer",self.open_trailer)
        self.actions=[self.watch,action("+ List",self.list_menu),self.trailer,action("Share",self.share),action("AniList",self.open_anilist)]
        for i,button in enumerate(self.actions):self.buttons.addWidget(button,0,i)
        body.addLayout(self.buttons)
        self.info_toggle=action("Show anime information",self.toggle_facts,"ghost")
        body.addWidget(self.info_toggle)
        self.compact_facts=text("","cinemaMuted");self.compact_facts.hide();body.addWidget(self.compact_facts)
        self.facts_expanded=False
        self.watch.clicked.disconnect()
        self.watch.clicked.connect(lambda:self.window()._watch_anime(self.resume))
        tabs=QHBoxLayout();self.tabs={}
        for i,caption in enumerate(("Episodes","Related","More like this")):
            b=action(caption,lambda checked=False,index=i:self.select_tab(index),"cinemaNav");b.setCheckable(True)
            self.tabs[i]=b;tabs.addWidget(b)
        tabs.addStretch();body.addLayout(tabs)
        self.panes=QStackedWidget();body.addWidget(self.panes)
        episodes=QWidget();episodes.setObjectName("surfaceContent");epbody=QVBoxLayout(episodes);epbody.setContentsMargins(0,0,0,0)
        row=QHBoxLayout();self.search_episode=QLineEdit();self.search_episode.setPlaceholderText("Jump to episode number…")
        self.search_episode.returnPressed.connect(self.jump_episode)
        row.addWidget(self.search_episode,1);row.addWidget(self.ranges);epbody.addLayout(row);epbody.addWidget(self.episode_note)
        self.episodes_layout=QGridLayout();self.episodes_layout.setSpacing(14);epbody.addLayout(self.episodes_layout);epbody.addStretch()
        self.panes.addWidget(episodes)
        self.related_grid=PosterGrid();self.recommendation_grid=PosterGrid()
        for grid in (self.related_grid,self.recommendation_grid):
            grid.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            grid.itemClicked.connect(lambda item:self.open(item.data(Qt.UserRole)))
            grid.itemActivated.connect(lambda item:self.open(item.data(Qt.UserRole)))
            grid.status_requested.connect(lambda entry,pos:self.window()._library_modern.show_status_menu(entry,pos))
            self.panes.addWidget(grid)
        self.related_layout=QGridLayout()  # base resize has no legacy cards to move
        self.split.addWidget(self.rail,0,0);self.split.addWidget(self.right,0,1);self.split.setColumnStretch(1,1)
        self.select_tab(0)
        old.deleteLater()
        self.resume={};self.progress_state={"episodes":{},"progress":0,"total":0}

    def open(self, entry, remember=True):
        self.select_tab(0)
        self.search_episode.clear()
        super().open(entry,remember)

    def select_tab(self,index):
        self.panes.setCurrentIndex(index)
        for i,b in self.tabs.items():b.setChecked(i==index)
        self.reflow()
        if hasattr(self,"visible_artwork_timer"):self.visible_artwork_timer.start()

    def refresh_artwork(self):
        super().refresh_artwork()
        fallback=self.hero.url or self.poster.url
        for _,art,_,_,_ in self.cards:
            art.fallback_url=fallback
        for grid in (self.related_grid,self.recommendation_grid):
            if grid.isVisible():grid.viewport().update()

    def render(self):
        e=self.entry
        w=self.window()
        self.progress_state=playback_state(e,w.controller.watchlog.history(w.controller.recent.items(),copy_result=False),w._library_modern.entries)
        self.resume=resume_entry(e,self.progress_state)
        self.title.setText(e.get("title") or "Unknown")
        kind=self.resume.get("resume_kind")
        self.watch.setText(f"Next · Ep {self.resume['episode']}" if kind=="next" else
                           "Watch again" if kind=="caught_up" else f"Continue · Ep {self.resume['episode']}" if e.get("episode") or self.resume.get("current_time") else "Watch now")
        self.trailer.setVisible(bool(e.get("trailer_url")))
        self.refresh_airing()
        self.metadata.setText("   ·   ".join(str(v) for v in (
            f"Score {e['mean_score']/10:g}" if e.get("mean_score") else "", e.get("season_year"),
            f"{e['episodes']} episodes" if e.get("episodes") else "",e.get("format"),*(e.get("genres") or [])[:5]) if v))
        self.render_synopsis()
        aired=e.get("start_date") or {}
        self.facts.setText("\n\n".join(f"{label}\n{value}" for label,value in (
            ("Format",e.get("format")),("Episodes",e.get("episodes")),("Duration",f"{e['duration']} min per episode" if e.get("duration") else ""),
            ("Aired","–".join(str(aired.get(k) or "?") for k in ("year","month","day")) if aired else ""),
            ("Studio",", ".join(e.get("studios") or [])),("Season",e.get("season")),
            ("Status",str(e.get("status") or "").replace("_"," ").title()),
            ("Tags"," · ".join((e.get("tags") or [])[:25])),("Native",e.get("native_title"))) if value))
        self.compact_facts.setText(self.facts.text())
        self.ranges.blockSignals(True);self.ranges.clear()
        self.episode_count=int(e.get("episodes") or e.get("episodes_total") or max(0,(e.get("next_episode") or 1)-1))
        for start in range(1,self.episode_count+1,12):self.ranges.addItem(f"{start}–{min(start+11,self.episode_count)}",start)
        target=max(1,int(self.resume.get("episode") or 1))
        if self.episode_count:self.ranges.setCurrentIndex(min(self.ranges.count()-1,(target-1)//12))
        self.ranges.blockSignals(False);self.render_episodes()
        for key,grid,index,caption in (("related",self.related_grid,1,"Related"),("recommendations",self.recommendation_grid,2,"More like this")):
            grid.clear()
            for entry in e.get(key) or []:
                item=QListWidgetItem();item.setData(Qt.UserRole,entry);item.setToolTip(entry["title"]);grid.addItem(item)
            self.tabs[index].setText(f"{caption}  {grid.count()}")
        self.tabs[0].setText(f"Episodes  {self.episode_count or '—'}")
        self.refresh_artwork()
        self.reflow()
        self.visible_artwork_timer.start()

    def render_episodes(self):
        clear(self.episodes_layout);self.cards=[]
        self.episode_note.setText("Episode metadata · TMDB" if self.window().cfg.data.tmdb_credential else
                                 "Connect TMDB in Settings → Artwork & metadata for episode images, descriptions and title logos.")
        start=self.ranges.currentData()
        if start is None:
            self.episodes_layout.addWidget(text("Episode information is not available yet.","cinemaMuted"),0,0);return
        for i,number in enumerate(range(start,min(self.episode_count+1,start+12))):
            card=QFrame();card.setObjectName("card");row=QHBoxLayout(card);row.setContentsMargins(10,10,10,10)
            art=Artwork();art.setMinimumWidth(130);art.setFixedHeight(145);row.addWidget(art,1)
            body=QVBoxLayout();title=text(f"Episode {number}","cardHeading");description=text("Description unavailable.","cinemaMuted")
            description.setMaximumHeight(80)
            body.addWidget(title);body.addWidget(description);body.addStretch()
            state=self.progress_state["episodes"].get(number,{})
            fraction=state.get("fraction")
            watched=state.get("watched") or (number<=self.progress_state["progress"] and fraction is None)
            art.watched=bool(watched)
            art.setAccessibleName(f"Episode {number} · "+("Watched" if watched else "Not watched"))
            badge="✓ Watched" if watched else f"Resume · {fraction:.0%}" if fraction is not None and fraction>0 else "Not watched"
            body.addWidget(text(badge,"cinemaMuted"))
            watch=action(f"Watch · Ep {number}",lambda checked=False,n=number:self.watch_episode(n),"ghost")
            body.addWidget(watch);row.addLayout(body,1)
            self.cards.append((number,art,title,description,card));self.episodes_layout.addWidget(card,i//self.columns(),i%self.columns())
        self.refresh_artwork()

    def columns(self):
        return 2 if hasattr(self,"right") and self.right.width()>=880 else 1

    def jump_episode(self):
        try:number=int(self.search_episode.text())
        except ValueError:return
        if 1<=number<=self.episode_count:
            self.ranges.setCurrentIndex((number-1)//12)
            self.search_episode.clear()

    def list_menu(self):
        library=self.window()._library_modern
        entry=next((e for e in library.entries if e.get("anilist_id")==self.entry.get("anilist_id")),self.entry)
        library.show_status_menu(entry,self.watch.mapToGlobal(self.watch.rect().bottomLeft()))

    def share(self):
        identity=self.entry.get("anilist_id")
        if identity:
            QApplication.clipboard().setText(f"https://anilist.co/anime/{identity}")
            self.notice.setText("AniList link copied")

    def open_anilist(self):
        if self.entry.get("anilist_id"):webbrowser.open(f"https://anilist.co/anime/{self.entry['anilist_id']}")

    def open_trailer(self):
        url=self.entry.get("trailer_url") or ""
        if url.startswith(("https://www.youtube.com/watch?v=","https://www.dailymotion.com/video/")):webbrowser.open(url)

    def toggle_facts(self):
        self.facts_expanded=not self.facts_expanded
        self.reflow()

    def refresh_airing(self):
        stamp=self.entry.get("airing_at")
        episode=self.entry.get("next_episode")
        self.airing.setVisible(bool(stamp and episode))
        if stamp and episode:
            remaining=max(0,int(stamp-time.time()))
            days,remainder=divmod(remaining,86400);hours,minutes=divmod(remainder//60,60)
            countdown=f"{days}d {hours}h {minutes}m" if days else f"{hours}h {minutes}m"
            self.airing.setText(f"Episode {episode} · "+(f"Airs in {countdown}" if remaining else "Scheduled airing has passed · refresh details")+
                               f"\n{datetime.fromtimestamp(stamp).strftime('%b %d, %Y · %H:%M')} local time")

    def showEvent(self,event):
        super().showEvent(event)
        if hasattr(self,"airing_timer"):self.airing_timer.start();self.refresh_airing()
        if hasattr(self,"visible_artwork_timer"):self.visible_artwork_timer.start()

    def hideEvent(self,event):
        if hasattr(self,"airing_timer"):self.airing_timer.stop()
        super().hideEvent(event)

    def reflow(self):
        if not hasattr(self,"rail"):return
        self.rail.setVisible(self.viewport().width()>=980)
        narrow=self.viewport().width()<980
        self.info_toggle.setVisible(narrow)
        self.info_toggle.setText("Hide anime information" if self.facts_expanded else "Show anime information")
        self.compact_facts.setVisible(narrow and self.facts_expanded)
        width=self.right.width()
        cols=3 if width<700 else 5
        visible=[b for b in self.actions if b is not self.trailer or bool(self.entry.get("trailer_url"))]
        for i,b in enumerate(visible):self.buttons.addWidget(b,i//cols,i%cols)
        for grid in (self.related_grid,self.recommendation_grid):
            grid.reflow()
            columns=max(1,(grid.viewport().width()-1)//max(1,grid.gridSize().width()))
            grid.setFixedHeight(max(120,math.ceil(grid.count()/columns)*grid.gridSize().height()+12))
        active=self.panes.currentWidget()
        for i in range(self.panes.count()):
            page=self.panes.widget(i)
            page.setSizePolicy(QSizePolicy.Preferred,QSizePolicy.Preferred if page is active else QSizePolicy.Ignored)
        self.panes.setMinimumHeight(active.minimumSizeHint().height() if self.panes.currentIndex()==0 else active.height())
        for i,(_,_,_,_,card) in enumerate(self.cards):
            self.episodes_layout.addWidget(card,i//self.columns(),i%self.columns())

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.reflow()
        if hasattr(self,"visible_artwork_timer"):self.visible_artwork_timer.start()
