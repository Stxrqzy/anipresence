"""Typed configuration with JSON persistence.

The config file lives in the OS app-data directory (%APPDATA%\\AniPresence on
Windows) so it survives updates and doesn't pollute the source tree. A
--config CLI flag can point at an explicit file instead.
"""
import json
import os
import re
import threading
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

APP_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "AniPresence"

_URL_RE = re.compile(r"https?://[^\s'\"]+", re.IGNORECASE)


def redact_urls(text):
    """Replace each URL in *text* with a neutral placeholder."""
    return _URL_RE.sub("[url]", text)


@dataclass
class Config:
    """User-facing knobs. Field names are used as JSON keys."""
    discord_application_id: str = ""
    chrome_debugging_port: int = 9222
    poll_interval_seconds: float = 5.0
    chrome_auto_launch: bool = True
    browser: str = "chrome"
    chrome_path: str = ""
    anilist_cache_ttl_days: int = 7
    show_paused_presence: bool = True
    rpc_enabled: bool = True
    rpc_broadcast: str = "discord"    # "discord" | "vencord" | "both"
    detection_enabled: bool = True
    hide_when_no_video: bool = True
    min_match_score: float = 60.0
    theme: str = "dark"
    accent: str = "#7c5cff"
    auto_accent: bool = True     # sample the accent from the current cover art
    compact: bool = False
    minimize_to_tray: bool = True
    start_windows: bool = False
    privacy_mode: bool = False
    idle_presence: bool = False
    mica_backdrop: bool = True
    cinematic_backdrop: bool = True
    cinematic_style: str = "frosted"     # "frosted" | "gradient" backdrop render
    window_geometry: str = ""        # "x,y,width,height" of the last visible spot
    window_maximized: bool = False
    current_tab: int = 0
    overrides: dict = field(default_factory=dict)
    extension_enabled: bool = True
    extension_port: int = 8793
    extension_token: str = ""
    anilist_username: str = ""
    anilist_accounts: list = field(default_factory=list)
    anilist_sync_enabled: bool = False
    anilist_access_token: str = ""
    custom_sites: list = field(default_factory=list)   # [{"name": str, "host": str}]
    hide_adult_content: bool = True
    update_feed_url: str = ""
    onboarding_complete: bool = False
    hotkey_toggle: str = "Ctrl+Alt+A"        # show/hide the window
    hotkey_pause: str = "Ctrl+Alt+P"         # pause/resume auto-detection
    hotkey_clear: str = "Ctrl+Alt+X"         # clear the presence
    hotkey_next: str = "Ctrl+Alt+N"          # bump to the next episode


class ConfigManager:
    """Load/save the :class:`Config` dataclass as JSON, thread-safely."""

    def __init__(self, path=None):
        self._lock = threading.Lock()
        self.path = Path(path) if path else (APP_DIR / "config.json")
        self.data = Config()
        self.load()

    def load(self):
        """Read config from disk; silently ignore corrupt/missing files."""
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            valid = {f.name: raw[f.name] for f in fields(Config) if f.name in raw}
            self.data = Config(**valid)
        except (json.JSONDecodeError, TypeError, ValueError):
            return
        # Pre-wizard configs (no onboarding key) are treated as already done so
        # upgrading users never see the first-run wizard.
        if "onboarding_complete" not in raw:
            self.data.onboarding_complete = True
        # Migrate the pre-account single-username config: it becomes one account.
        if not self.data.anilist_accounts and self.data.anilist_username.strip():
            self.data.anilist_accounts = [self.data.anilist_username.strip()]

    def save(self):
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(asdict(self.data), indent=2), encoding="utf-8"
            )

    def set(self, **kwargs):
        """Update one or more fields and persist only if something changed."""
        changed = False
        for key, value in kwargs.items():
            if key in {f.name for f in fields(Config)} and getattr(self.data, key) != value:
                setattr(self.data, key, value)
                changed = True
        if changed:
            self.save()
