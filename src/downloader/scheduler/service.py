"""Recurring maintenance: subscription checks, yt-dlp updates, library verification."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from PySide6.QtCore import QObject, QTimer, Signal

from downloader.config.settings import SettingsStore
from downloader.core import binaries
from downloader.core.workers import run_async
from downloader.library.service import LibraryService
from downloader.subscriptions.sync import SubscriptionService

log = logging.getLogger(__name__)

YTDLP_UPDATE_EVERY = timedelta(hours=24)
VERIFY_EVERY = timedelta(hours=6)


class MaintenanceScheduler(QObject):
    ytdlp_updated = Signal(str)  # message
    ytdlp_update_failed = Signal(str)

    TICK_MS = 60_000

    def __init__(self, settings: SettingsStore, subscriptions: SubscriptionService,
                 library: LibraryService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.subscriptions = subscriptions
        self.library = library
        self._last_verify: datetime | None = None
        self._updating = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.tick)

    def start(self) -> None:
        self._timer.start(self.TICK_MS)
        QTimer.singleShot(5_000, self.tick)  # let the UI settle first

    def tick(self) -> None:
        self.subscriptions.check_due()
        self._maybe_update_ytdlp()
        self._maybe_verify_library()

    def _maybe_update_ytdlp(self, force: bool = False) -> None:
        s = self.settings.data
        if self._updating or not (s.auto_update_ytdlp or force):
            return
        try:
            last = datetime.fromisoformat(s.last_ytdlp_update) if s.last_ytdlp_update else None
        except ValueError:
            last = None
        if not force and last and datetime.now() - last < YTDLP_UPDATE_EVERY:
            return
        self.update_ytdlp()

    def update_ytdlp(self) -> None:
        if self._updating:
            return
        self._updating = True
        channel = self.settings.data.ytdlp_channel

        def done(message: str) -> None:
            self._updating = False
            self.settings.update(last_ytdlp_update=datetime.now().isoformat(timespec="seconds"))
            log.info("yt-dlp update: %s", message)
            self.ytdlp_updated.emit(message)

        def failed(exc: Exception) -> None:
            self._updating = False
            log.warning("yt-dlp update failed: %s", exc)
            self.ytdlp_update_failed.emit(str(exc))

        run_async(lambda: binaries.update_ytdlp(channel), on_done=done, on_error=failed)

    def _maybe_verify_library(self) -> None:
        now = datetime.now()
        if self._last_verify and now - self._last_verify < VERIFY_EVERY:
            return
        self._last_verify = now
        run_async(self.library.verify_files)
