"""Compact, keyboard-accessible Settings sections."""
from PySide6.QtWidgets import QFrame, QPushButton, QVBoxLayout, QStyle


class SettingsSection(QFrame):
    def __init__(self, title, content, expanded=False):
        super().__init__()
        self.setObjectName("settingsSection")
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(4)
        self.toggle = QPushButton(title.replace("&", "&&"))
        self.toggle.setObjectName("ghost"); self.toggle.setCheckable(True); self.toggle.setChecked(expanded)
        self.toggle.setAccessibleName(title)
        self.toggle.setStyleSheet("text-align: left; padding: 10px 14px;")
        self.toggle.setIcon(self.style().standardIcon(QStyle.SP_ArrowDown if expanded else QStyle.SP_ArrowRight))
        layout.addWidget(self.toggle); layout.addWidget(content)
        self.content = content
        content.setVisible(expanded)
        self.toggle.toggled.connect(content.setVisible)
        self.toggle.toggled.connect(lambda on: self.toggle.setIcon(
            self.style().standardIcon(QStyle.SP_ArrowDown if on else QStyle.SP_ArrowRight)))

    def expand(self):
        self.toggle.setChecked(True)
