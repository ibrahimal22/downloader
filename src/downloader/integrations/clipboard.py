"""Clipboard watcher: offers to download when a video link is copied."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QGuiApplication

from downloader.config.settings import SettingsStore

# Hosts where a copied link is very likely a video. Other links are ignored to avoid noise.
VIDEO_HOSTS = (
    "youtube.com", "youtu.be", "vimeo.com", "dailymotion.com", "twitch.tv", "tiktok.com",
    "x.com", "twitter.com", "instagram.com", "facebook.com", "fb.watch", "reddit.com",
    "soundcloud.com", "bandcamp.com", "bilibili.com", "rumble.com", "odysee.com",
    "streamable.com", "vk.com", "ok.ru", "nicovideo.jp", "ted.com", "archive.org",
    "bitchute.com", "kick.com", "mixcloud.com", "9gag.com", "imgur.com",
)
URL_RE = re.compile(r"^\s*(https?://\S+)\s*$", re.IGNORECASE)


def video_url(text: str) -> str | None:
    match = URL_RE.match(text or "")
    if not match:
        return None
    url = match.group(1)
    host = (urlparse(url).hostname or "").lower()
    if any(host == h or host.endswith("." + h) for h in VIDEO_HOSTS):
        return url
    return None


class ClipboardWatcher(QObject):
    url_detected = Signal(str)

    def __init__(self, settings: SettingsStore, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._last: str | None = None
        self._ignore_next = False
        QGuiApplication.clipboard().dataChanged.connect(self._on_change)

    def ignore_next(self) -> None:
        """Call before the app itself writes to the clipboard."""
        self._ignore_next = True

    def _on_change(self) -> None:
        if self._ignore_next:
            self._ignore_next = False
            return
        if not self.settings.data.clipboard_monitor:
            return
        url = video_url(QGuiApplication.clipboard().text())
        if url and url != self._last:
            self._last = url
            self.url_detected.emit(url)
