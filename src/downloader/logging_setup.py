"""Rotating file + console logging."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from downloader import paths

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(level)
    # httpx logs full URLs at INFO, including signed download tokens.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)

    file_handler = RotatingFileHandler(
        paths.logs_dir() / "downloader.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(FORMAT))
    root.addHandler(file_handler)

    if sys.stderr is not None:  # windowed PyInstaller builds have no stderr
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter(FORMAT))
        root.addHandler(console)

    def excepthook(exc_type, exc, tb):
        logging.getLogger("crash").critical("Unhandled exception", exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = excepthook
