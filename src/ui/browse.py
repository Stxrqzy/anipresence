"""Responsive, asynchronously paginated anime discovery."""
from datetime import date
from PySide6.QtCore import Qt, Signal, QTimer, QSignalBlocker
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLineEdit, QComboBox, QPushButton, QLabel, QListWidgetItem
from .library import PosterGrid
from ..anime.browse import catalogue, recommendations
from .tasks import LatestTask
from ..core.media_identity import media_id


class BrowsePanel(QWidget):
    result_ready = Signal(int, int, object)

    def __init__(self):
        super().__init__()
        self.generation = 0
        self.page = 0
        self.loading = False
        self.entries = []
        self.has_next = False
        self.started = False
        self.discovery_mode="trending"
        self._library_key=None
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(12)
        self.heading = QLabel("Browse anime");self.heading.setObjectName("cinemaHeading");self.heading.hide()
        layout.addWidget(self.heading)
        self.discovery=QWidget();self.discovery_row=QGridLayout(self.discovery);self.discovery_row.setContentsMargins(0,0,0,0)
        self.discovery_buttons={}
        for key,caption in (("trending","Trending"),("airing","Airing now"),("upcoming","Upcoming"),("personal","For you")):
            button=QPushButton(caption);button.setCheckable(True);button.setObjectName("cinemaNav")
            button.clicked.connect(lambda checked=False,mode=key:self.select_discovery(mode))
            self.discovery_buttons[key]=button;self.discovery_row.addWidget(button,0,len(self.discovery_buttons)-1)
        layout.addWidget(self.discovery);self.discovery.hide()
        row = QHBoxLayout()
        self.toolbar = row
        self.search = QLineEdit(); self.search.setPlaceholderText("Search anime…"); self.search.setClearButtonEnabled(True)
        self.sort = QComboBox()
        for title, value in (("Trending", "TRENDING_DESC"), ("Popular", "POPULARITY_DESC"), ("Highest rated", "SCORE_DESC"),
                             ("Newest", "START_DATE_DESC"), ("Title A–Z", "TITLE_ENGLISH")):
            self.sort.addItem(title, value)
        self.filters_button = QPushButton("Filters"); self.filters_button.setCheckable(True)
        reset = QPushButton("Reset"); reset.clicked.connect(self.reset)
        row.addWidget(self.search, 1); row.addWidget(self.sort); row.addWidget(self.filters_button); row.addWidget(reset)
        layout.addLayout(row)
        self.filter_box = QWidget(); self.filters_layout = QGridLayout(self.filter_box); self.filters_layout.setContentsMargins(0, 0, 0, 0)
        self.controls = {}
        options = {
            "genre": ("Genres", ["Action", "Adventure", "Comedy", "Drama", "Ecchi", "Fantasy", "Horror", "Mahou Shoujo", "Mecha", "Music", "Mystery", "Psychological", "Romance", "Sci-Fi", "Slice of Life", "Sports", "Supernatural", "Thriller"]),
            "tag": ("Tags", ["Isekai", "Shounen", "Seinen", "School", "Super Power", "Time Manipulation", "Vampire", "Space", "Found Family"]),
            "year": ("Year", [str(y) for y in range(date.today().year+2, 1949, -1)]),
            "season": ("Season", ["WINTER", "SPRING", "SUMMER", "FALL"]),
            "format": ("Format", ["TV", "TV_SHORT", "MOVIE", "SPECIAL", "OVA", "ONA", "MUSIC"]),
            "status": ("Status", ["RELEASING", "FINISHED", "NOT_YET_RELEASED", "CANCELLED", "HIATUS"]),
            "country": ("Origin", [("Japan", "JP"), ("China", "CN"), ("South Korea", "KR"), ("Taiwan", "TW")]),
            "score": ("Minimum score", [("7+", 69), ("8+", 79), ("9+", 89)]),
        }
        for key, (caption, values) in options.items():
            control = QComboBox(); control.addItem(caption, None)
            for value in values:
                title, data = value if isinstance(value, tuple) else (value.replace("_", " ").title() if key in ("season", "status") else value, value)
                control.addItem(title, data)
            control.setToolTip(caption); control.setMinimumWidth(100)
            control.currentIndexChanged.connect(self.schedule)
            self.controls[key] = control
        self.controls["tag"].setEditable(True)
        self.controls["tag"].lineEdit().textEdited.connect(self.schedule)
        self.studio = QLineEdit(); self.studio.setPlaceholderText("Studio, e.g. Kyoto Animation"); self.studio.textEdited.connect(self.schedule)
        self.controls["studio"] = self.studio
        self.controls["country"].setToolTip("Country of origin. AniList does not catalogue dubbed audio availability.")
        layout.addWidget(self.filter_box)
        self.filters_button.setChecked(True)
        self.filters_button.toggled.connect(self.filter_box.setVisible)
        self.message = QLabel("Discover anime from AniList"); self.message.setObjectName("subLabel"); self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.grid = PosterGrid(); layout.addWidget(self.grid, 1)
        self.grid.itemClicked.connect(lambda item: self.window()._open_anime(item.data(Qt.UserRole)))
        self.grid.itemActivated.connect(lambda item: self.window()._open_anime(item.data(Qt.UserRole)))
        self.grid.status_requested.connect(lambda entry, pos: self.window()._library_modern.show_status_menu(entry, pos))
        self.more = QPushButton("Load more"); self.more.hide(); self.more.clicked.connect(self.load_more); layout.addWidget(self.more)
        self.grid.verticalScrollBar().valueChanged.connect(self.near_bottom)
        self.timer = QTimer(self); self.timer.setSingleShot(True); self.timer.setInterval(400); self.timer.timeout.connect(self.reload)
        self.search.textChanged.connect(self.schedule); self.sort.currentIndexChanged.connect(self.schedule)
        self.result_ready.connect(self.receive)
        self.task=LatestTask(self)
        self.task.completed.connect(lambda token,result:self.result_ready.emit(*token,result))

    def configure_layout(self, cinema):
        self.cinema = cinema
        self.heading.hide()  # The shared illustrated header now supplies the page title.
        self.discovery.setVisible(cinema)
        for key,button in self.discovery_buttons.items():button.setChecked(key==self.discovery_mode)
        self.search.setMinimumHeight(42 if cinema else 0)
        self.search.setMaximumWidth(340 if cinema else 16777215)
        for control in self.controls.values():
            control.setObjectName("cinemaFilter" if cinema else "")
        self.grid.reflow()
        self.reflow_filters()

    def filters(self):
        values = {key: control.currentData() for key, control in self.controls.items() if isinstance(control, QComboBox)}
        tag = self.controls["tag"]
        values["tag"] = tag.currentText().strip() if tag.currentText() != "Tags" else None
        if values["year"]: values["year"] = int(values["year"])
        values.update(search=self.search.text().strip(), sort=[self.sort.currentData()], studio=self.studio.text().strip(),
                      adult=False if self.window().cfg.data.hide_adult_content else None)
        return values

    def ensure_loaded(self):
        if not self.started:
            self.reload()

    def select_discovery(self,mode):
        controls=[self.search,self.sort,*self.controls.values()]
        blockers=[QSignalBlocker(control) for control in controls]
        self.search.clear();self.studio.clear();self.sort.setCurrentIndex(0)
        for control in self.controls.values():
            if isinstance(control,QComboBox):control.setCurrentIndex(0)
        if mode in ("airing","upcoming"):
            control=self.controls["status"]
            control.setCurrentIndex(control.findData("RELEASING" if mode=="airing" else "NOT_YET_RELEASED"))
            if mode=="upcoming":self.sort.setCurrentIndex(self.sort.findData("POPULARITY_DESC"))
        del blockers
        self.discovery_mode=mode
        self._library_key=self.library_key()
        for key,button in self.discovery_buttons.items():button.setChecked(key==mode)
        self.reload()

    def library_key(self):
        return tuple((media_id(e),e.get("status"),e.get("progress"),e.get("score")) for e in self.window()._library_modern.entries)

    def library_changed(self):
        key=self.library_key()
        if self.discovery_mode=="personal" and key!=self._library_key:
            self._library_key=key;self.generation+=1;self.started=False
            self.entries=[];self.grid.clear();self.has_next=False;self.more.hide()
            if self.isVisible():self.reload()

    def schedule(self, *_):
        # Invalidate old responses immediately, including during the debounce.
        self.generation += 1
        self.discovery_mode="search"
        for button in self.discovery_buttons.values():button.setChecked(False)
        self.loading = True
        self.more.setEnabled(False)
        self.timer.start()

    def reset(self):
        self.search.clear(); self.studio.clear(); self.sort.setCurrentIndex(0)
        for control in self.controls.values():
            if isinstance(control, QComboBox): control.setCurrentIndex(0)
        self.schedule()

    def reload(self):
        self.timer.stop()
        self.generation += 1; self.page = 0; self.entries = []; self.grid.clear(); self.has_next = False
        self.started = True
        self.request(1)

    def request(self, page):
        self.loading = True; self.more.setEnabled(False); self.message.setText("Searching AniList…")
        generation = self.generation; filters = self.filters()
        if self.discovery_mode=="personal":
            library=[dict(e) for e in self.window()._library_modern.entries]
            hide_adult=self.window().cfg.data.hide_adult_content
            self.task.submit((generation,page),lambda:recommendations(library,hide_adult))
        else:self.task.submit((generation,page),lambda:catalogue(filters,page))

    def receive(self, generation, page, result):
        if generation != self.generation: return
        self.loading = False; self.more.setEnabled(True)
        if result.get("error"):
            self.message.setText("Could not load anime: " + result["error"])
            self.more.setText("Retry"); self.more.show()
            return
        self.page = page; self.has_next = bool(result.get("hasNextPage"))
        known = {media_id(e) for e in self.entries}
        for entry in result["entries"]:
            if media_id(entry) not in known:
                self.entries.append(entry); known.add(media_id(entry))
                item = QListWidgetItem(); item.setData(Qt.UserRole, entry); self.grid.addItem(item)
        self.sync_library()
        total = result.get("total")
        self.message.setText(f"{len(self.entries):,} shown" + (f" · {total:,}{'+' if total >= 5000 else ''} catalogue matches" if total else "") if self.entries else "No matching anime. Try different filters.")
        if result.get("note"):self.message.setText(result["note"])
        self.more.setText("Load more"); self.more.setVisible(self.has_next)
        self.window()._scrolling.install()

    def sync_library(self):
        sections = self.window().controller.watchlog.anilist_sections()
        saved = {media_id(e): dict(e, library_section=section) for section, rows in sections.items() for e in rows}
        # Also use confirmed UI payloads, which may arrive before persisted refreshes.
        for e in self.window()._library_modern.entries: saved[media_id(e)] = e
        for i, entry in enumerate(self.entries):
            membership = saved.get(media_id(entry), {})
            row = dict(entry, status=membership.get("status", ""), library_section=membership.get("library_section", ""),
                       progress=membership.get("progress", 0))
            self.grid.item(i).setData(Qt.UserRole, row)
            self.grid.item(i).setToolTip(row["title"]+("\n"+row["suggestion_reason"] if row.get("suggestion_reason") else ""))
        self.grid.viewport().update()

    def load_more(self):
        if not self.loading: self.request(self.page+1)

    def near_bottom(self, value):
        if self.has_next and not self.loading and value > 0 and value >= self.grid.verticalScrollBar().maximum()-180:
            self.load_more()

    def resizeEvent(self, event):
        self.reflow_filters()
        super().resizeEvent(event)

    def reflow_filters(self):
        inline = getattr(self,"cinema",False) and self.width()>=1400
        if inline != getattr(self,"_inline_filters",None):
            self._inline_filters = inline
            self.layout().removeWidget(self.filter_box);self.toolbar.removeWidget(self.filter_box)
            if inline:self.toolbar.insertWidget(2,self.filter_box,4)
            else:self.layout().insertWidget(3,self.filter_box)
            self.filter_box.setVisible(inline or self.filters_button.isChecked())
            self.filters_button.setVisible(not inline)
        columns = len(self.controls) if inline else max(2, min(5, self.width()//170))
        for i, control in enumerate(self.controls.values()):
            self.filters_layout.removeWidget(control); self.filters_layout.addWidget(control, i//columns, i%columns)
        for i in range(len(self.controls)):
            self.filters_layout.setColumnStretch(i,1 if i<columns else 0)
        for i,button in enumerate(self.discovery_buttons.values()):
            self.discovery_row.addWidget(button,i//2 if self.width()<550 else 0,i%2 if self.width()<550 else i)
