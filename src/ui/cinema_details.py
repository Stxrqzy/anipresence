"""V3 cinematic details: persistent facts rail, episode tiles and catalogue tabs."""
import webbrowser
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPainter, QLinearGradient
from PySide6.QtWidgets import (QApplication, QFrame, QGridLayout, QHBoxLayout, QLineEdit,
                              QListWidgetItem, QStackedWidget, QVBoxLayout, QWidget)
from .details import DetailsPage, Artwork, text
from .cinema import action, clear
from .library import PosterGrid
from .poster import draw_filled_pixmap


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
        buttons=QHBoxLayout();self.watch.setObjectName("cinemaPrimary");buttons.addWidget(self.watch)
        buttons.addWidget(action("+ List",self.list_menu))
        buttons.addWidget(action("Share",self.share))
        buttons.addWidget(action("AniList",self.open_anilist));buttons.addStretch();body.addLayout(buttons)
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
            grid.setMinimumHeight(540)
            grid.itemClicked.connect(lambda item:self.open(item.data(Qt.UserRole)))
            grid.itemActivated.connect(lambda item:self.open(item.data(Qt.UserRole)))
            grid.status_requested.connect(lambda entry,pos:self.window()._library_modern.show_status_menu(entry,pos))
            self.panes.addWidget(grid)
        self.related_layout=QGridLayout()  # base resize has no legacy cards to move
        self.split.addWidget(self.rail,0,0);self.split.addWidget(self.right,0,1);self.split.setColumnStretch(1,1)
        self.select_tab(0)
        old.deleteLater()

    def open(self, entry, remember=True):
        self.select_tab(0)
        self.search_episode.clear()
        super().open(entry,remember)

    def select_tab(self,index):
        self.panes.setCurrentIndex(index)
        for i,b in self.tabs.items():b.setChecked(i==index)

    def refresh_artwork(self):
        super().refresh_artwork()
        for grid in (self.related_grid,self.recommendation_grid):
            if grid.isVisible():grid.viewport().update()

    def render(self):
        e=self.entry
        self.title.setText(e.get("title") or "Unknown")
        self.watch.setText(f"Continue · Ep {e['episode']}" if e.get("episode") else "Watch now")
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
        self.ranges.blockSignals(True);self.ranges.clear()
        self.episode_count=int(e.get("episodes") or e.get("episodes_total") or max(0,(e.get("next_episode") or 1)-1))
        for start in range(1,self.episode_count+1,12):self.ranges.addItem(f"{start}–{min(start+11,self.episode_count)}",start)
        self.ranges.blockSignals(False);self.render_episodes()
        for key,grid,index,caption in (("related",self.related_grid,1,"Related"),("recommendations",self.recommendation_grid,2,"More like this")):
            grid.clear()
            for entry in e.get(key) or []:
                item=QListWidgetItem();item.setData(Qt.UserRole,entry);item.setToolTip(entry["title"]);grid.addItem(item)
            self.tabs[index].setText(f"{caption}  {grid.count()}")
        self.tabs[0].setText(f"Episodes  {self.episode_count or '—'}")
        self.refresh_artwork()

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
            watch=action(f"Watch · Ep {number}",lambda checked=False,n=number:self.watch_episode(n),"ghost")
            body.addWidget(watch);row.addLayout(body,1)
            self.cards.append((number,art,title,description,card));self.episodes_layout.addWidget(card,i//self.columns(),i%self.columns())
        self.refresh_artwork()

    def columns(self):
        return 2 if self.viewport().width()>=1250 else 1

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

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,"rail"):self.rail.setVisible(self.viewport().width()>=850)
