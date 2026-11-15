"""Keyboard-first local search with debounced, bounded catalogue requests."""
from PySide6.QtCore import Qt, QEvent, QTimer
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QLabel, QListWidget, QListWidgetItem, QPushButton
from ..anime.browse import catalogue
from ..core.media_identity import MediaIdentity, media_id, normalized_title
from .tasks import LatestTask


def search_index(library,history):
    resolver=MediaIdentity([*library,*history])
    rows={}
    for entry in library:
        key=resolver.key(entry)
        rows[key]=dict(entry,search_source="Library")
    seen=set()
    for entry in history:
        key=resolver.key(entry)
        if key in seen:continue
        seen.add(key)
        base=rows.get(key,{})
        rows[key]={**base,**entry,"search_source":"Library · History" if base else "History"}
        rows[key]["aliases"]=list(dict.fromkeys([*(base.get("aliases") or []),*(entry.get("aliases") or [])]))
    return [(normalized_title(" ".join([e.get("title") or "",e.get("title_english") or "",*(e.get("aliases") or [])])),e)
            for e in rows.values()]


def local_matches(index,query,limit=20):
    query=normalized_title(query)
    words=query.split()
    matched=[e for text,e in index if all(word in text for word in words)]
    if query:matched.sort(key=lambda e:(not normalized_title(e.get("title")).startswith(query),normalized_title(e.get("title"))))
    return matched[:limit]


class QuickSearch(QDialog):
    def __init__(self,window):
        super().__init__(window)
        self.owner=window;self.generation=0;self.index=[];self.local=[]
        self.setWindowTitle("Quick search");self.setObjectName("quickSearchDialog")
        self.setWindowModality(Qt.WindowModal)
        layout=QVBoxLayout(self);layout.setContentsMargins(20,20,20,20);layout.setSpacing(12)
        self.search=QLineEdit();self.search.setPlaceholderText("Search library, history and AniList…")
        self.search.setClearButtonEnabled(True);self.search.installEventFilter(self)
        layout.addWidget(self.search)
        self.results=QListWidget();self.results.setWordWrap(True);layout.addWidget(self.results,1)
        footer=QHBoxLayout();self.status=QLabel();self.status.setWordWrap(True);footer.addWidget(self.status,1)
        self.retry=QPushButton("Retry");self.retry.clicked.connect(self.request);footer.addWidget(self.retry);self.retry.hide()
        layout.addLayout(footer)
        hint=QLabel("↑ ↓ to select · Enter to open details · Esc to close");layout.addWidget(hint)
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(350);self.timer.timeout.connect(self.request)
        self.task=LatestTask(self);self.task.completed.connect(self.receive)
        self.search.textChanged.connect(self.changed)
        self.search.returnPressed.connect(self.activate_current)
        self.results.itemActivated.connect(self.activate)
        self.results.itemClicked.connect(self.activate)
        self.finished.connect(self.closed)

    def present(self):
        w=self.owner
        self.index=search_index(w._library_modern.entries,w.controller.watchlog.history(w.controller.recent.items(),copy_result=False))
        light=w._applied_theme=="light"
        base,panel,ink=("#fafafa","#eeeeee","#141414") if light else ("#101010","#202020","#eeeeee")
        self.setStyleSheet(f"""QDialog#quickSearchDialog {{background:{base};color:{ink};}}
          QDialog#quickSearchDialog QLabel {{color:{ink};}}
          QDialog#quickSearchDialog QLineEdit, QDialog#quickSearchDialog QListWidget {{
            background:{panel};color:{ink};border:0;border-radius:10px;padding:10px;}}
          QDialog#quickSearchDialog QListWidget::item {{padding:12px;}}
          QDialog#quickSearchDialog QListWidget::item:selected {{background:#63519b;color:white;}}""")
        self.resize(min(720,w.width()-40),max(280,min(600,w.height()-80)))
        self.show();self.raise_();self.activateWindow();self.search.setFocus();self.search.selectAll()
        self.changed()

    def changed(self):
        self.generation+=1;self.timer.stop();self.task.cancel_pending();self.retry.hide()
        query=self.search.text().strip()
        self.local=local_matches(self.index,query)
        self.render(self.local)
        if len(query)>=2:
            self.status.setText("Local matches ready · Searching AniList…");self.timer.start()
        else:self.status.setText("Type at least two characters to search AniList." if self.local else "Search by title or an alternate title.")

    def request(self):
        query=self.search.text().strip()
        if len(query)<2 or not self.isVisible():return
        self.retry.hide();self.status.setText("Searching AniList…")
        filters={"search":query,"adult":False if self.owner.cfg.data.hide_adult_content else None,"sort":["SEARCH_MATCH"]}
        self.task.submit(self.generation,lambda:catalogue(filters,1))

    def receive(self,generation,result):
        if generation!=self.generation or not self.isVisible():return
        if result.get("error"):
            self.status.setText("AniList unavailable. Local results are still available.");self.retry.show();return
        ids={media_id(e) for e in self.local if media_id(e)}
        names={normalized_title(e.get("title")) for e in self.local}
        remote=[dict(e,search_source="AniList") for e in result.get("entries",[])
                if media_id(e) not in ids and normalized_title(e.get("title")) not in names][:15]
        self.render([*self.local,*remote])
        self.status.setText(f"{len(self.local)} local · {len(remote)} catalogue results" if self.local or remote else "No matches. Try another title.")

    def render(self,entries):
        self.results.clear()
        for entry in entries:
            item=QListWidgetItem(f"{entry.get('title') or 'Unknown'}\n{entry['search_source']}")
            item.setData(Qt.UserRole,entry);self.results.addItem(item)
        if entries:self.results.setCurrentRow(0)

    def activate_current(self):
        if self.results.currentItem():self.activate(self.results.currentItem())

    def activate(self,item):
        entry=item.data(Qt.UserRole)
        if entry:self.accept();self.owner._open_anime(entry)

    def closed(self,*_):
        self.generation+=1;self.timer.stop();self.task.cancel_pending()

    def eventFilter(self,watched,event):
        if watched is self.search and event.type()==QEvent.KeyPress and event.key() in (Qt.Key_Down,Qt.Key_Up):
            if self.results.count():
                self.results.setFocus()
                self.results.setCurrentRow(0 if event.key()==Qt.Key_Down else self.results.count()-1)
            return True
        return super().eventFilter(watched,event)
