"""Reference-inspired illustrated surfaces and accessible animated switches."""
import math
from PySide6.QtCore import Property, QEasingCurve, QPointF, QRectF, QSize, Qt, QPropertyAnimation
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient, QPixmap
from PySide6.QtWidgets import QCheckBox, QFrame, QWidget


def paint_landscape(painter, rect, accent, light=False, variant=0):
    """Resolution-independent moonlit landscape, with space left for headings."""
    painter.save()
    opacity = painter.opacity()
    painter.setClipRect(rect)
    painter.translate(rect.topLeft())
    w, h = rect.width(), rect.height()
    tint = QColor(accent)
    cx, cy = w * .79, h * .34
    glow = QRadialGradient(cx, cy, h * 1.2)
    glow.setColorAt(0, QColor(tint.red(), tint.green(), tint.blue(), 85 if not light else 40))
    glow.setColorAt(1, QColor(0, 0, 0, 0))
    painter.fillRect(QRectF(0, 0, w, h), glow)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#b4a1ee" if variant % 2 == 0 else "#bb668d"))
    painter.setOpacity(opacity * (.55 if not light else .25))
    painter.drawEllipse(QPointF(cx, cy), h * .28, h * .28)
    painter.setOpacity(opacity * .16)
    painter.setBrush(QColor("#35254d"))
    for x, y, radius in ((-.12, -.08, .08), (.12, .1, .12), (.04, -.16, .05)):
        painter.drawEllipse(QPointF(cx + x*h, cy+y*h), radius*h, radius*h)
    painter.setOpacity(opacity)
    for layer in range(3):
        path = QPainterPath(QPointF(0, h))
        for i in range(41):
            x = w*i/40
            y = h*(.68+layer*.12) + math.sin(i*.7+layer+variant)*h*.07 + math.cos(i*.31)*h*.08
            path.lineTo(x, y)
        path.lineTo(w, h)
        painter.setBrush(QColor(19+layer*3, 16+layer*2, 37+layer*5, 170))
        painter.drawPath(path)
    # A torii and pine silhouettes frame the moon without obscuring UI text.
    painter.setPen(QPen(QColor(10, 10, 23, 210), max(2, h*.022)))
    x = w*.87
    for dx in (-h*.15, h*.15):
        painter.drawLine(QPointF(x+dx, h*.48), QPointF(x+dx*.85, h*.96))
    painter.drawLine(QPointF(x-h*.23, h*.49), QPointF(x+h*.23, h*.49))
    painter.drawLine(QPointF(x-h*.2, h*.59), QPointF(x+h*.2, h*.59))
    for pos, size in ((.62,.35),(.96,.46),(.56,.22)):
        bx, by, height = w*pos, h*.98, h*size
        painter.setPen(QPen(QColor(10,10,23,210), 2))
        painter.drawLine(QPointF(bx, by), QPointF(bx, by-height))
        for level in range(4):
            yy = by-height+height*.2*level
            extent = height*(.12+.055*level)
            path = QPainterPath(QPointF(bx, yy-height*.2))
            path.lineTo(bx-extent, yy+height*.18)
            path.lineTo(bx+extent, yy+height*.18)
            path.closeSubpath()
            painter.setBrush(QColor(10,10,23,210))
            painter.drawPath(path)
    veil = QLinearGradient(0,0,w,0)
    base = QColor("#eef0f8" if light else "#0e0d1b")
    veil.setColorAt(0, base)
    base.setAlpha(235); veil.setColorAt(.25, base)
    base.setAlpha(0); veil.setColorAt(.65, base)
    painter.fillRect(QRectF(0,0,w,h), veil)
    painter.restore()


class IllustratedHeader(QWidget):
    def __init__(self):
        super().__init__()
        self.accent = "#9955ff"
        self.light = False
        self.variant = 0
        self.art = QPixmap()
        self.setMinimumHeight(112)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        paint_landscape(painter, QRectF(self.rect()), self.accent, self.light, self.variant)
        if not self.art.isNull():
            pixmap=self.art.scaled(self.size(),Qt.KeepAspectRatioByExpanding,Qt.SmoothTransformation)
            painter.setOpacity(.5 if not self.light else .2)
            painter.drawPixmap((self.width()-pixmap.width())//2,(self.height()-pixmap.height())//2,pixmap)
            painter.setOpacity(1)
            veil=QLinearGradient(0,0,self.width(),0)
            color=QColor("#eef0f8" if self.light else "#0e0d1b")
            veil.setColorAt(0,color); color.setAlpha(210); veil.setColorAt(.38,color)
            color.setAlpha(0); veil.setColorAt(1,color)
            painter.fillRect(self.rect(),veil)


class IllustratedSidebar(QFrame):
    def paintEvent(self,event):
        super().paintEvent(event)
        window=self.window()
        if not hasattr(window,"cfg") or window.cfg.data.ui_layout!="modern":return
        painter=QPainter(self); painter.setRenderHint(QPainter.Antialiasing)
        clip=QPainterPath();clip.addRoundedRect(QRectF(self.rect().adjusted(1,1,-1,-1)),15,15);painter.setClipPath(clip)
        painter.setOpacity(.18)
        paint_landscape(painter,QRectF(0,self.height()*.36,self.width(),self.height()*.49),
                        window.cfg.data.accent,window._applied_theme=="light",1)


class Toggle(QCheckBox):
    """A real checkbox, painted as a switch only in the modern layout."""
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._position = 0.0
        self.animation = QPropertyAnimation(self, b"position", self)
        self.animation.setDuration(140)
        self.animation.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)

    def _modern(self):
        window = self.window()
        return hasattr(window, "cfg") and window.cfg.data.ui_layout == "modern"

    def _animate(self, checked):
        self.animation.stop()
        if self.isVisible() and self.window().cfg.data.smooth_animations:
            self.animation.setStartValue(self._position)
            self.animation.setEndValue(float(checked))
            self.animation.start()
        else:
            self.position = float(checked)

    def _get_position(self): return self._position
    def _set_position(self, value):
        self._position = value
        self.update()
    position = Property(float, _get_position, _set_position)

    def sizeHint(self):
        if not self._modern():
            return super().sizeHint()
        return QSize(self.fontMetrics().horizontalAdvance(self.text().replace("&&", "&")) + 56, 30)

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, event):
        if not self._modern():
            return super().paintEvent(event)
        if self.animation.state() != QPropertyAnimation.Running:
            self._position = float(self.isChecked())
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        y = (self.height()-18)/2
        accent = QColor(self.window().cfg.data.accent)
        p.setPen(QPen(accent.lighter(130) if self.isChecked() else QColor("#55516d"), 1))
        p.setBrush(accent if self.isChecked() else QColor("#343247"))
        p.drawRoundedRect(QRectF(0, y, 34, 18), 9, 9)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#ffffff" if self.isChecked() else "#afa9ca"))
        p.drawEllipse(QRectF(3+16*self._position, y+3, 12, 12))
        p.setPen(self.palette().windowText().color())
        p.drawText(self.rect().adjusted(46,0,0,0), Qt.AlignVCenter, self.text().replace("&&", "&"))
        if self.hasFocus():
            p.setPen(QPen(accent, 1)); p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(self.rect().adjusted(0,1,-1,-1), 5, 5)
