"""Startup splash screen: the logo over a loading bar while the app boots."""
from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QTimer, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QProgressBar, QVBoxLayout, QWidget


class SplashWindow(QWidget):
    """Frameless, rounded startup card with the logo and a loading bar.

    Call :meth:`start` after showing it (kicks off the slow fill), then
    :meth:`complete` once everything is loaded: it animates the bar to 100%
    and fades the card out before running ``on_done`` (typically showing the
    main window).
    """

    def __init__(self, accent="", theme="dark"):
        super().__init__()
        from ..theme import resolve_theme
        dark = resolve_theme(theme) == "dark"
        self._accent = QColor(accent or "#7c5cff")
        if not self._accent.isValid():
            self._accent = QColor("#7c5cff")
        self._bg_color = QColor(20, 22, 32) if dark else QColor(250, 250, 252)
        self._sub_color = "#9aa3bd" if dark else "#6b7280"
        self._bar_bg = "rgba(255, 255, 255, 0.14)" if dark else "rgba(0, 0, 0, 0.10)"

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(480, 260)

        from ..logo import OrbitWordmark

        logo_label = OrbitWordmark()
        logo_label.setFixedHeight(112)

        self._bar = QProgressBar()
        self._bar.setObjectName("splashBar")
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(8)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 26)
        layout.setSpacing(12)
        layout.addStretch(1)
        layout.addWidget(logo_label)
        layout.addSpacing(14)
        layout.addWidget(self._bar)
        layout.addStretch(1)

        self.setStyleSheet(
            f"""
            QProgressBar#splashBar {{ background-color: {self._bar_bg};
                border: none; border-radius: 4px; }}
            QProgressBar#splashBar::chunk {{ background-color: {self._accent.name()};
                border-radius: 4px; }}
            """
        )
        self._bar.setObjectName("splashBar")

        self._on_done = None
        self._pulse = QTimer(self)
        self._pulse.setInterval(90)
        self._pulse.timeout.connect(self._pulse_tick)

    # ----------------------------------------------------------------- UI
    def paintEvent(self, event):  # noqa: N802 (Qt naming)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(255, 255, 255, 0), 0))
        p.setBrush(self._bg_color)
        p.drawRoundedRect(self.rect(), 22, 22)
        p.setPen(QPen(QColor(self._accent.red(), self._accent.green(),
                             self._accent.blue(), 70), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 21, 21)

    # -------------------------------------------------------------- control
    def _pulse_tick(self):
        if self._bar.value() < 84:
            self._bar.setValue(self._bar.value() + 3)

    def start(self):
        """Begin filling the bar (slow crawl) so startup feels alive."""
        self._pulse.start()

    def complete(self, on_done):
        """Fill the bar to 100%, fade the splash out, then call *on_done*."""
        self._on_done = on_done
        self._pulse.stop()
        anim = QPropertyAnimation(self._bar, b"value", self)
        anim.setDuration(900)
        anim.setStartValue(self._bar.value())
        anim.setEndValue(100)
        anim.setEasingCurve(QEasingCurve.InOutCubic)
        anim.finished.connect(self._fade_out)
        self._bar_anim = anim
        anim.start()

    def _fade_out(self):
        self._bar_anim = None
        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setDuration(260)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.InCubic)
        fade.finished.connect(self._close_done)
        self._fade_anim = fade
        fade.start()

    def _close_done(self):
        self._fade_anim = None
        self.setWindowOpacity(1.0)
        self.hide()
        self.close()
        cb = self._on_done
        self._on_done = None
        if cb is not None:
            cb()
