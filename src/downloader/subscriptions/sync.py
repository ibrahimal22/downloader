"""Playlist/channel subscriptions: periodic checks that queue only new videos."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal
from sqlalchemy import select

from downloader import paths
from downloader.config.settings import SettingsStore
from downloader.core import ytdlp
from downloader.core.download_manager import DownloadManager, NewDownload
from downloader.core.fsutil import sanitize_component
from downloader.core.workers import run_async
from downloader.db.models import Subscription
from downloader.db.session import session_scope
from downloader.library.service import LibraryService

log = logging.getLogger(__name__)

# Filter keys stored in Subscription.filters
#   min_duration / max_duration (seconds), include_keywords / exclude_keywords (comma lists),
#   skip_shorts (bool), date_after ("YYYYMMDD"), numbering (bool: mirror playlist order)


def archive_path(subscription_id: int) -> Path:
    return paths.archives_dir() / f"subscription-{subscription_id}.txt"


def read_archive(subscription_id: int) -> set[str]:
    path = archive_path(subscription_id)
    if not path.exists():
        return set()
    ids = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2:
            ids.add(parts[1])
    return ids


def append_archive(subscription_id: int, extractor: str, ids: list[str]) -> None:
    key = (extractor or "generic").lower().replace("tab", "")  # YoutubeTab -> youtube
    with archive_path(subscription_id).open("a", encoding="utf-8") as fh:
        for vid in ids:
            fh.write(f"{key} {vid}\n")


def _keywords(value: str | None) -> list[str]:
    return [k.strip().lower() for k in (value or "").split(",") if k.strip()]


def entry_passes(entry: ytdlp.ProbeEntry, filters: dict[str, Any]) -> bool:
    title = (entry.title or "").lower()
    duration = entry.duration
    if filters.get("min_duration") and duration is not None and duration < filters["min_duration"]:
        return False
    if filters.get("max_duration") and duration is not None and duration > filters["max_duration"]:
        return False
    include = _keywords(filters.get("include_keywords"))
    if include and not any(k in title for k in include):
        return False
    if any(k in title for k in _keywords(filters.get("exclude_keywords"))):
        return False
    return not (filters.get("skip_shorts") and ("/shorts/" in entry.url or (
        duration is not None and duration <= 60 and "#shorts" in title)))


def download_args(filters: dict[str, Any]) -> list[str]:
    """Filters that need full metadata are enforced by yt-dlp at download time."""
    args: list[str] = []
    date_after = str(filters.get("date_after") or "")
    if re.fullmatch(r"\d{8}", date_after):
        args += ["--dateafter", date_after]
    if filters.get("skip_shorts"):
        args += ["--match-filters", "original_url!*=/shorts/"]
    return args


class SubscriptionService(QObject):
    changed = Signal()
    checked = Signal(int, int, str)  # subscription id, videos queued, error ("" if ok)

    def __init__(self, settings: SettingsStore, manager: DownloadManager,
                 library: LibraryService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.manager = manager
        self.library = library
        self._checking: set[int] = set()
        manager.completed.connect(self._on_download_completed)

    # ------------------------------------------------------------------ CRUD

    def all(self) -> list[Subscription]:
        with session_scope() as s:
            rows = list(s.scalars(select(Subscription).order_by(Subscription.name)).all())
            s.expunge_all()
        return rows

    def get(self, sub_id: int) -> Subscription | None:
        with session_scope() as s:
            sub = s.get(Subscription, sub_id)
            if sub:
                s.expunge(sub)
            return sub

    def create(self, name: str, url: str, **fields: Any) -> Subscription:
        fields.setdefault("output_root",
                          str(Path(self.settings.data.download_dir) / sanitize_component(name)))
        fields.setdefault("preset", self.settings.data.default_preset)
        fields.setdefault("interval_minutes", 360)
        fields.setdefault("filters", {})
        fields.setdefault("keep_last", 0)
        fields.setdefault("from_now_on", False)
        fields.setdefault("enabled", True)
        with session_scope() as s:
            sub = Subscription(name=name, url=url, **fields)
            s.add(sub)
            s.flush()
            s.expunge(sub)
        self.changed.emit()
        return sub

    def update(self, sub_id: int, **fields: Any) -> None:
        with session_scope() as s:
            sub = s.get(Subscription, sub_id)
            if sub:
                for key, value in fields.items():
                    setattr(sub, key, value)
        self.changed.emit()

    def delete(self, sub_id: int) -> None:
        with session_scope() as s:
            sub = s.get(Subscription, sub_id)
            if sub:
                s.delete(sub)
        archive_path(sub_id).unlink(missing_ok=True)
        self.changed.emit()

    def is_checking(self, sub_id: int) -> bool:
        return sub_id in self._checking

    # ------------------------------------------------------------------ checking

    def check_due(self) -> None:
        now = datetime.now()
        for sub in self.all():
            if not sub.enabled or sub.id in self._checking:
                continue
            due = sub.last_checked is None or \
                sub.last_checked + timedelta(minutes=sub.interval_minutes) <= now
            if due:
                self.check(sub.id)

    def check(self, sub_id: int) -> None:
        sub = self.get(sub_id)
        if sub is None or sub_id in self._checking:
            return
        self._checking.add(sub_id)
        self.changed.emit()
        settings = self.settings.data
        run_async(
            lambda: ytdlp.probe(sub.url, settings, timeout=600),
            on_done=lambda result: self._on_probed(sub_id, result),
            on_error=lambda exc: self._on_probe_failed(sub_id, exc),
        )

    def _on_probe_failed(self, sub_id: int, exc: Exception) -> None:
        self._checking.discard(sub_id)
        self.update(sub_id, last_checked=datetime.now(), last_error=str(exc))
        log.warning("Subscription %s check failed: %s", sub_id, exc)
        self.checked.emit(sub_id, 0, str(exc))

    def _on_probed(self, sub_id: int, result: ytdlp.ProbeResult) -> None:
        self._checking.discard(sub_id)
        sub = self.get(sub_id)
        if sub is None:
            return
        entries = result.entries if result.is_playlist else [ytdlp.ProbeEntry(
            url=result.url, id=result.video_id, title=result.title, duration=result.duration,
            thumbnail=result.thumbnail, uploader=result.uploader, index=1)]
        extractor = (result.extractor or "generic").replace("Tab", "")

        archived = read_archive(sub_id)
        fresh = [e for e in entries if e.id and e.id not in archived]

        if sub.last_checked is None and sub.from_now_on:
            # First check with "from now on": remember everything that exists, download nothing.
            append_archive(sub_id, extractor, [e.id for e in fresh if e.id])
            self.update(sub_id, last_checked=datetime.now(), last_error=None)
            self.checked.emit(sub_id, 0, "")
            return

        filters = sub.filters or {}
        wanted = [e for e in fresh if entry_passes(e, filters)
                  and not self.library.is_duplicate(e.id, extractor)]
        numbering = bool(filters.get("numbering", result.is_playlist))
        new = [
            NewDownload(
                url=e.url, output_root=sub.output_root, preset=sub.preset, title=e.title,
                uploader=e.uploader or result.uploader, extractor=extractor, video_id=e.id,
                duration=e.duration, thumbnail_url=e.thumbnail,
                playlist_title=sub.name if numbering else None,
                playlist_index=e.index if numbering else None,
                template_kind="playlist" if numbering else "single",
                subscription_id=sub_id, extra_args=download_args(filters),
            )
            for e in wanted
        ]
        added, _skipped = self.manager.add(new)
        self.update(sub_id, last_checked=datetime.now(), last_error=None)
        log.info("Subscription %s: %d new, %d queued", sub.name, len(fresh), len(added))
        self.checked.emit(sub_id, len(added), "")

    # ------------------------------------------------------------------ retention

    def _on_download_completed(self, item_id: int, _info: dict) -> None:
        item = self.manager.get(item_id)
        if item and item.subscription_id:
            self.apply_retention(item.subscription_id)

    def apply_retention(self, sub_id: int) -> int:
        sub = self.get(sub_id)
        if not sub or sub.keep_last <= 0:
            return 0
        items = self.library.items_for_subscription(sub_id)
        excess = items[sub.keep_last:]
        if excess:
            self.library.delete([i.id for i in excess], delete_files=True)
            log.info("Subscription %s: removed %d old videos", sub.name, len(excess))
        return len(excess)
