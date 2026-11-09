"""First-run onboarding wizard.

Shown only on a fresh install (or an aborted first run) to guide the user
through the three things the app needs to be useful: a Discord Application ID,
browser auto-launch, and an optional AniList account. Everything it writes goes
through :class:`ConfigManager` / :class:`Controller`, so the wizard is purely a
UI front-end and can be closed or skipped at any point.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget,
)

from ..logo import OrbitWordmark

BROWSER_INDEX = {"chrome": 0, "brave": 1, "edge": 2, "opera": 3}
BROWSER_VALUE = {v: k for k, v in BROWSER_INDEX.items()}
BROWSER_NAMES = {"chrome": "Chrome", "brave": "Brave", "edge": "Edge", "opera": "Opera"}


def _title(text):
    label = QLabel(text)
    label.setObjectName("appTitle")
    label.setWordWrap(True)
    return label


def _body(text):
    label = QLabel(text)
    label.setObjectName("subLabel")
    label.setWordWrap(True)
    return label


class OnboardingWizard(QDialog):
    def __init__(self, cfg, controller, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.controller = controller
        self.setWindowTitle("Welcome to AniPresence")
        self.setModal(True)
        self.setMinimumSize(480, 460)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        self.pages = [
            self._page_welcome(),
            self._page_discord(),
            self._page_browser(),
            self._page_anilist(),
            self._page_done(),
        ]
        for page in self.pages:
            self.stack.addWidget(page)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        self.back_btn = QPushButton("Back")
        self.back_btn.setObjectName("ghost")
        self.back_btn.clicked.connect(lambda: self._go(-1))
        buttons.addWidget(self.back_btn)
        self.next_btn = QPushButton("Next")
        self.next_btn.clicked.connect(self._advance)
        buttons.addWidget(self.next_btn)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("ghost")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(cancel_btn)
        layout.addLayout(buttons)

        self._page = 0
        self._update_buttons()

    # ------------------------------------------------------------ pages
    def _page_welcome(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(12)
        icon_label = OrbitWordmark()
        icon_label.setFixedHeight(100)
        v.addWidget(icon_label)
        v.addWidget(_title("Welcome to AniPresence"))
        v.addWidget(_body(
            "AniPresence detects the anime playing in your browser and mirrors "
            "it to Discord as a Rich Presence. A few quick steps and you're set."
        ))
        v.addStretch(1)
        return page

    def _page_discord(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(10)
        v.addWidget(_title("Discord Rich Presence"))
        v.addWidget(_body(
            "Paste your Discord application's ID below. You can create one in "
            "seconds at the Discord Developer Portal — an unverified app shows "
            "the presence on your own profile."
        ))
        self.discord_id_edit = QLineEdit()
        self.discord_id_edit.setPlaceholderText("Paste your Application ID")
        self.discord_id_edit.setText(self.cfg.data.discord_application_id)
        v.addWidget(self.discord_id_edit)
        get_id = QPushButton("Open Discord Developer Portal")
        get_id.setObjectName("ghost")
        get_id.clicked.connect(
            lambda: __import__("webbrowser").open(
                "https://discord.com/developers/applications"
            )
        )
        v.addWidget(get_id)
        self.rpc_check = QCheckBox("Show my activity in Discord")
        self.rpc_check.setChecked(self.cfg.data.rpc_enabled)
        v.addWidget(self.rpc_check)
        v.addStretch(1)
        return page

    def _page_browser(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(10)
        v.addWidget(_title("Browser"))
        v.addWidget(_body(
            "AniPresence reads the video player through the browser's debugging "
            "port. It can launch the browser for you automatically."
        ))
        self.launch_chrome_check = QCheckBox("Launch my browser automatically")
        self.launch_chrome_check.setChecked(self.cfg.data.chrome_auto_launch)
        v.addWidget(self.launch_chrome_check)
        v.addWidget(QLabel("Browser"))
        self.browser_combo = QComboBox()
        self.browser_combo.addItems(["Chrome", "Brave", "Edge", "Opera"])
        self.browser_combo.setCurrentIndex(BROWSER_INDEX.get(self.cfg.data.browser, 0))
        v.addWidget(self.browser_combo)
        v.addStretch(1)
        return page

    def _page_anilist(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(10)
        v.addWidget(_title("AniList (optional)"))
        v.addWidget(_body(
            "Enter your public AniList username to import your watch history "
            "into the Stats tab. Each account keeps its own save file, and you "
            "can add more accounts later from Controls."
        ))
        self.anilist_edit = QLineEdit()
        self.anilist_edit.setPlaceholderText("Your public AniList username")
        self.anilist_edit.setText(
            self.controller.active_anilist_account
            or self.cfg.data.anilist_username
        )
        v.addWidget(self.anilist_edit)
        v.addStretch(1)
        return page

    def _page_done(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(12)
        v.addWidget(_title("You're all set"))
        self.summary_label = _body("")
        v.addWidget(self.summary_label)
        v.addWidget(_body(
            "You can tweak everything later from Controls, and reopen your "
            "stats under the Stats tab."
        ))
        v.addStretch(1)
        return page

    # ------------------------------------------------------------ nav
    def _advance(self):
        if self._page < len(self.pages) - 1:
            self._go(1)
        else:
            self._finish()

    def _go(self, delta):
        self._page = max(0, min(len(self.pages) - 1, self._page + delta))
        self.stack.setCurrentIndex(self._page)
        self._update_buttons()

    def _update_buttons(self):
        self.back_btn.setEnabled(self._page > 0)
        is_last = self._page == len(self.pages) - 1
        self.next_btn.setText("Get started" if is_last else "Next")

    # ------------------------------------------------------------ apply
    def _finish(self):
        app_id = self.discord_id_edit.text().strip()
        self.cfg.set(
            discord_application_id=app_id,
            rpc_enabled=self.rpc_check.isChecked(),
            chrome_auto_launch=self.launch_chrome_check.isChecked(),
            browser=BROWSER_VALUE.get(self.browser_combo.currentIndex(), "chrome"),
            onboarding_complete=True,
        )
        username = self.anilist_edit.text().strip()
        if username:
            if username not in self.controller.anilist_accounts:
                self.controller.add_anilist_account(username)
            else:
                self.controller.switch_anilist_account(username)
        self.controller.reconnect_discord()
        self.accept()
