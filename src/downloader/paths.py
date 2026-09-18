"""Well-known filesystem locations used by the app."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from platformdirs import PlatformDirs

APP_NAME = "Downloader"

# appauthor=False avoids a redundant "Downloader\Downloader" nesting on Windows.
_dirs = PlatformDirs(APP_NAME, appauthor=False, roaming=False)

def _root(default: str) -> Path:
    # DOWNLOADER_HOME overrides everything (tests, portable installs).
    override = os.environ.get("DOWNLOADER_HOME")
    path = Path(override) if override else Path(default)
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_dir() -> Path:
    return _root(_dirs.user_data_dir)


def config_file() -> Path:
    return data_dir() / "settings.json"


def db_file() -> Path:
    return data_dir() / "downloader.db"


def logs_dir() -> Path:
    path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def bin_dir() -> Path:
    """Sidecar binaries (yt-dlp, ffmpeg, deno) live here so they can be updated in place."""
    path = data_dir() / "bin"
    path.mkdir(parents=True, exist_ok=True)
    return path


def archives_dir() -> Path:
    path = data_dir() / "archives"
    path.mkdir(parents=True, exist_ok=True)
    return path


def thumbs_dir() -> Path:
    path = data_dir() / "thumbnails"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_download_dir() -> Path:
    return Path.home() / "Videos" / APP_NAME


def resource_dir() -> Path:
    """Bundled read-only resources (themes, icons). Works under PyInstaller too."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "downloader"
    return Path(__file__).resolve().parent
