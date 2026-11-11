"""Smooth wheel scrolling without accidental edits to settings controls."""
from PySide6.QtCore import QObject, QEvent, QEasingCurve, QPropertyAnimation, Qt
from PySide6.QtWidgets import QAbstractScrollArea, QAbstractSpinBox, QComboBox, QSlider, QWidget


class ScrollController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.animations = {}

    def install(self):
        for area in self.window.findChildren(QAbstractScrollArea):
            # Popup option lists retain their native interaction.
            if area.window() is not self.window:
                continue
            area.viewport().installEventFilter(self)
            area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        for control in self.window.findChildren(QWidget):
            if not isinstance(control, (QComboBox, QAbstractSpinBox, QSlider)):
                continue
            control.installEventFilter(self)
            if isinstance(control, QAbstractSpinBox):
                control.lineEdit().installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() != QEvent.Wheel:
            return False
        parent = obj
        control = isinstance(obj, (QComboBox, QAbstractSpinBox, QSlider)) or isinstance(obj.parentWidget(), QAbstractSpinBox)
        if isinstance(obj, QComboBox) and obj.view().isVisible():
            return False
        area = None
        while parent is not None:
            if isinstance(parent, QAbstractScrollArea):
                area = parent
                bar = area.verticalScrollBar()
                direction = event.pixelDelta().y() or event.angleDelta().y()
                if (direction > 0 and bar.value() > bar.minimum()) or (direction < 0 and bar.value() < bar.maximum()):
                    break
            parent = parent.parentWidget()
        if area is None:
            return control
        bar = area.verticalScrollBar()
        animation = self.animations.get(bar)
        if animation is None:
            animation = QPropertyAnimation(bar, b"value", bar)
            animation.setEasingCurve(QEasingCurve.OutCubic)
            animation.setDuration(150)
            self.animations[bar] = animation
            bar.destroyed.connect(lambda: self.animations.pop(bar, None))
        pixel = event.pixelDelta().y()
        delta = pixel or event.angleDelta().y()/120*max(60, min(120, bar.singleStep()*3))
        target = animation.endValue() if animation.state() == QPropertyAnimation.Running else bar.value()
        animation.stop()
        target = max(bar.minimum(), min(bar.maximum(), round(target-delta)))
        if pixel or not self.window.cfg.data.smooth_animations:
            bar.setValue(target)
        else:
            animation.setStartValue(bar.value())
            animation.setEndValue(target)
            animation.start()
        event.accept()
        return True
