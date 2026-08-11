"""Logging setup shared by all modules.

Logs go to both the console and a rotating file inside the app data folder.
The UI additionally receives log lines through a dedicated Qt handler installed
by the controller worker thread.
"""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(log_dir=None, debug=False):
    """Configure the 'anipresence' logger hierarchy (idempotent)."""
    root = logging.getLogger("anipresence")
    if root.handlers:  # already configured (e.g. hot-reload guard)
        root.setLevel(logging.DEBUG if debug else logging.INFO)
        return root

    root.setLevel(logging.DEBUG if debug else logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    if log_dir:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            Path(log_dir) / "anipresence.log",
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)

    return root


def get_logger(name="anipresence"):
    """Return a child logger under the 'anipresence' namespace."""
    return logging.getLogger(name)
