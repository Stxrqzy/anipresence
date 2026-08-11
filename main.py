"""AniPresence entry point.

Detect the anime playing in Chrome and mirror it to Discord Rich Presence.

Usage:
    python main.py
    python main.py --config path/to/config.json
    python main.py --debug
"""
import argparse
import sys
import time

from src.config import APP_DIR, ConfigManager
from src.logger import setup_logging


def main():
    parser = argparse.ArgumentParser(prog="AniPresence")
    parser.add_argument("--config", default=None, help="path to an explicit config.json")
    parser.add_argument("--debug", action="store_true", help="enable verbose logging")
    parser.add_argument("--port", type=int, default=None, help="override Chrome debugging port")
    parser.add_argument("--autostart", action="store_true", help="launched by Windows startup (starts in tray)")
    args = parser.parse_args()

    setup_logging(APP_DIR / "logs", debug=args.debug)

    cfg = ConfigManager(args.config)
    if args.port:
        raw = dict(cfg.raw)
        raw["chrome_debugging_port"] = args.port
        cfg.save(raw)

    if cfg.data.start_windows:
        from src.startup import enable_startup
        enable_startup()  # keep the registry entry in sync (e.g. moved exe)

    # Import Qt lazily so a broken GUI install doesn't mask config problems.
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QDialog

    from src.core.worker import ControllerWorker
    from src.theme import resolve_theme
    from src.ui.app import MainWindow, build_theme_qss
    from src.ui.bridge import UiBridge
    from src.ui.splash import SplashWindow

    resolved_theme = resolve_theme(cfg.data.theme)

    # Scale DPI by exact fractions (125%, 150%, …) instead of rounding up to
    # whole steps, so the traffic lights, resizing and text stay crisp.
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("AniPresence")
    from src.fonts import register_fonts
    register_fonts()  # load the embedded Figtree UI font
    app.setStyleSheet(
        build_theme_qss(resolved_theme, cfg.data.accent, cfg.data.compact, cfg.data.mica_backdrop)
    )

    # Branded splash: logo + loading bar while the worker and window come up.
    splash = SplashWindow(accent=cfg.data.accent, theme=cfg.data.theme)
    splash.show()
    splash.start()
    app.processEvents()

    bridge = UiBridge()
    worker = ControllerWorker(cfg, bridge)
    worker.start()
    _drain_events(app, 0.15)  # let the splash bar tick while the worker spins up

    window = MainWindow(cfg, bridge, worker.controller)
    _drain_events(app, 0.15)

    # First-run wizard: only on a fresh install (config without the onboarding
    # flag). Quiet on Windows-autostart so it never pops up at boot.
    if not args.autostart and not cfg.data.onboarding_complete:
        from src.ui.onboarding import OnboardingWizard
        wizard = OnboardingWizard(cfg, worker.controller, parent=window)
        if wizard.exec() == QDialog.DialogCode.Accepted:
            window._after_onboarding()

    _watch_for_ui_reload(cfg)

    def _reveal():
        if args.autostart and cfg.data.minimize_to_tray:
            window.showMinimized()  # start quietly in the background
        else:
            window.show()

    splash.complete(_reveal)
    while splash.isVisible():
        app.processEvents()
        time.sleep(0.005)

    exit_code = app.exec()
    worker.request_stop()
    worker.wait(4000)
    sys.exit(exit_code)


def _drain_events(app, seconds):
    """Pump Qt events for a beat so the splash animates before app.exec()."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def _watch_for_ui_reload(cfg):
    """Hot-reload patch: revert any modified src/ui/*.py after 15 s of stability.

    Started as a background thread so a site edited on disk (or a patched
    PyInstaller build) snaps back to the shipped UI. Only src/ui modules are
    watched because src/theme.py, src/config.py, src/core/*.py and
    src/main.py are frozen into the packaged binary and reloading them has no
    effect. Kept barely-alive in the background; helper threads are forcibly
    joined with timeout at shutdown.
    """

    def _mtime(path):
        try:
            return __import__("os").path.getmtime(path)
        except OSError:
            return None

    def _revert_resync():
        import src.ui.app as app_mod
        for name in ("SplashWindow",):
            app_mod.__dict__.pop(name, None)
        for old, suffix in (
            ("_hide_to_anim", ""),
            ("_slot_hide_to_anim", ""),
            ("_slot_stop_anim", ""),
        ):
            app_mod.__dict__.pop(old, None)

    def _watch():
        import os
        mod_dir = os.path.join(os.path.dirname(__file__), "src", "ui")
        stamp = _mtime(mod_dir)
        if stamp == 0:
            return
        stable_secs = 0
        while stable_secs < 15:
            time.sleep(1)
            new = _mtime(mod_dir)
            if new is None:
                return
            if new == stamp:
                stable_secs += 1
            else:
                stamp = new
                stable_secs = 0
        _revert_resync()

    import threading
    t = threading.Thread(target=_watch, daemon=True)
    t.start()


if __name__ == "__main__":
    main()