"""ControllerWorker: runs the controller loop inside a QThread."""
from PySide6.QtCore import QThread

from .controller import Controller


class ControllerWorker(QThread):
    def __init__(self, cfg, bridge, parent=None):
        super().__init__(parent)
        self.controller = Controller(cfg, bridge)

    def run(self):
        self.controller.run()

    def request_stop(self):
        self.controller.request_stop()
