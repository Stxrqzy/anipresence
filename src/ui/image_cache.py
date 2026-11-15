"""Bounded decoded-image cache; disk reads and resizing stay off the UI thread."""
from collections import OrderedDict
from PySide6.QtCore import QObject, QRunnable, QThreadPool, QSize, Signal, Qt
from PySide6.QtGui import QImageReader, QPixmap


class _Result(QObject):
    ready = Signal(object, object)


class _Decode(QRunnable):
    def __init__(self, key, result):
        super().__init__()
        self.key, self.result = key, result

    def run(self):
        path, width, height = self.key
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid():
            reader.setScaledSize(size.scaled(QSize(width, height), Qt.KeepAspectRatioByExpanding).boundedTo(size))
        image = reader.read()
        self.result.ready.emit(self.key, image)


class ImageCache(QObject):
    ready = Signal()

    def __init__(self, parent=None, budget=96*1024*1024):
        super().__init__(parent)
        self.cache = OrderedDict()
        self.pending = set()
        self.failed = set()
        self.budget, self.bytes = budget, 0
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.result = _Result(self)
        self.result.ready.connect(self._loaded)

    def get(self, path, width=768, height=768):
        key = (str(path), int(width), int(height))
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key][0]
        if key not in self.pending and key not in self.failed and len(self.pending) < 24:
            self.pending.add(key)
            self.pool.start(_Decode(key, self.result))
        return QPixmap()

    def invalidate_missing(self,path=None):
        if path is None:self.failed.clear()
        else:self.failed={key for key in self.failed if key[0]!=str(path)}

    def _loaded(self, key, image):
        self.pending.discard(key)
        if image.isNull():
            self.failed.add(key)
        else:
            pixmap = QPixmap.fromImage(image)
            cost = image.sizeInBytes()
            while self.cache and self.bytes+cost > self.budget:
                _, (_, removed) = self.cache.popitem(last=False)
                self.bytes -= removed
            self.cache[key] = (pixmap, cost)
            self.bytes += cost
        self.ready.emit()
