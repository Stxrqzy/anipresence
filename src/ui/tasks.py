"""A bounded worker for interactive queries: one running, one newest pending."""
import threading
from PySide6.QtCore import QObject, Signal


class LatestTask(QObject):
    completed=Signal(object,object)
    _finished=Signal(object,object)

    def __init__(self,parent=None):
        super().__init__(parent);self.running=False;self.pending=None
        self._finished.connect(self._receive)

    def submit(self,token,work):
        if self.running:
            self.pending=(token,work)
        else:self._start(token,work)

    def cancel_pending(self):
        self.pending=None

    def _start(self,token,work):
        self.running=True
        signal=self._finished
        def run():
            try:result=work()
            except Exception as exc:result={"error":str(exc)}
            try:signal.emit(token,result)
            except RuntimeError:pass
        threading.Thread(target=run,daemon=True,name="interactive-query").start()

    def _receive(self,token,result):
        self.running=False
        pending,self.pending=self.pending,None
        if pending:self._start(*pending)
        self.completed.emit(token,result)
