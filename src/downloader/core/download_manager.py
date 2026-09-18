"""The download queue: ordering, concurrency, scheduling, retries and persistence."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from urllib.parse import urlparse

from PySide6.QtCore import QObject, QTimer, Signal
from sqlalchemy import func, select

from downloader import paths
from downloader.config.settings import SettingsStore
from downloader.core import fsutil, ytdlp
from downloader.core.download_task import DownloadTask
from downloader.db.models import DownloadItem, Status
from downloader.db.session import session_scope
from downloader.scheduler import conditions, rules

log = logging.getLogger(__name__)

RETRY_BASE_SECONDS = 30


@dataclass
class NewDownload:
    url: str
    output_root: str
    preset: str = "best"
    title: str | None = None
    uploader: str | None = None
    extractor: str | None = None
    video_id: str | None = None
    duration: float | None = None
    thumbnail_url: str | None = None
    playlist_title: str | None = None
    playlist_index: int | None = None
    template_kind: str = "single"
    scheduled_at: datetime | None = None
    respect_window: bool = True
    priority: int = 0
    subscription_id: int | None = None
    extra_args: list[str] = field(default_factory=list)


def site_key(item: DownloadItem) -> str:
    if item.extractor:
        return item.extractor.lower()
    host = urlparse(item.url).hostname or ""
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


class DownloadManager(QObject):
    added = Signal(int)
    changed = Signal(int)
    removed = Signal(int)
    completed = Signal(int, dict)  # item id, yt-dlp "done" info
    failed = Signal(int, str)
    queue_finished = Signal()
    stats = Signal(dict)  # {"active", "queued", "speed", "limit_kbps", "blocked_reason"}

    TICK_MS = 1000

    def __init__(
        self,
        settings: SettingsStore,
        is_duplicate: Callable[[str | None, str | None], bool] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.is_duplicate = is_duplicate or (lambda _vid, _ext: False)
        self.items: dict[int, DownloadItem] = {}
        self.tasks: dict[int, DownloadTask] = {}
        self._dirty: set[int] = set()
        self._had_work = False
        self._last_limit: int | None = None
        self._paused_all = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.tick)

    # ------------------------------------------------------------------ lifecycle

    def load(self) -> None:
        """Load unfinished items; anything that was mid-download resumes."""
        with session_scope() as s:
            rows = s.scalars(select(DownloadItem)).all()
            for item in rows:
                if item.status in (Status.DOWNLOADING, Status.PROCESSING):
                    item.status = Status.QUEUED
                    item.stage = "Resuming"
                self.items[item.id] = item
            s.expunge_all()
        log.info("Loaded %d downloads", len(self.items))

    def start(self) -> None:
        self._timer.start(self.TICK_MS)
        self.tick()

    def shutdown(self) -> None:
        """Stop processes but keep them resumable on next launch."""
        self._timer.stop()
        for task in list(self.tasks.values()):
            task.stop("shutdown")
        self._flush()

    # ------------------------------------------------------------------ queries

    def get(self, item_id: int) -> DownloadItem | None:
        return self.items.get(item_id)

    def task(self, item_id: int) -> DownloadTask | None:
        return self.tasks.get(item_id)

    def ordered(self, include_finished: bool = False) -> list[DownloadItem]:
        items = [i for i in self.items.values() if include_finished or not i.status.is_finished]
        return sorted(items, key=lambda i: (-i.priority, i.position, i.id))

    def finished(self) -> list[DownloadItem]:
        items = [i for i in self.items.values() if i.status.is_finished]
        return sorted(items, key=lambda i: i.finished_at or i.created_at, reverse=True)

    def has_pending(self, video_id: str | None) -> bool:
        return bool(video_id) and any(
            i.video_id == video_id and not i.status.is_finished for i in self.items.values()
        )

    # ------------------------------------------------------------------ mutations

    def add(self, new: Iterable[NewDownload],
            skip_duplicates: bool | None = None) -> tuple[list[int], int]:
        """Queue downloads. Returns (added ids, number skipped as duplicates).

        skip_duplicates=None uses the user setting; pass False for explicit re-downloads.
        """
        skip_dupes = self.settings.data.skip_duplicates if skip_duplicates is None \
            else skip_duplicates
        added: list[int] = []
        skipped = 0
        with session_scope() as s:
            position = (s.scalar(select(func.max(DownloadItem.position))) or 0) + 1
            for nd in new:
                if skip_dupes and nd.video_id and (
                    self.has_pending(nd.video_id) or self.is_duplicate(nd.video_id, nd.extractor)
                ):
                    skipped += 1
                    continue
                item = DownloadItem(
                    url=nd.url, title=nd.title, uploader=nd.uploader, extractor=nd.extractor,
                    video_id=nd.video_id, duration=nd.duration, thumbnail_url=nd.thumbnail_url,
                    playlist_title=nd.playlist_title, playlist_index=nd.playlist_index,
                    preset=nd.preset, output_root=nd.output_root, template_kind=nd.template_kind,
                    extra_args=list(nd.extra_args), respect_window=nd.respect_window,
                    priority=nd.priority, position=position, subscription_id=nd.subscription_id,
                    scheduled_at=nd.scheduled_at,
                    status=Status.SCHEDULED if nd.scheduled_at else Status.QUEUED,
                    progress=0.0, downloaded_bytes=0, retries=0,
                    created_at=datetime.now(),
                )
                position += 1
                s.add(item)
                s.flush()
                added.append(item.id)
                self.items[item.id] = item
            s.expunge_all()
        for item_id in added:
            self.added.emit(item_id)
        if added:
            QTimer.singleShot(0, self.tick)
        return added, skipped

    def _set(self, item: DownloadItem, **changes) -> None:
        for key, value in changes.items():
            setattr(item, key, value)
        self._dirty.add(item.id)
        self.changed.emit(item.id)

    def pause(self, ids: Iterable[int]) -> None:
        for item_id in ids:
            item = self.items.get(item_id)
            if not item or item.status.is_finished:
                continue
            if item_id in self.tasks:
                self.tasks[item_id].stop("pause")
            else:
                self._set(item, status=Status.PAUSED, stage="Paused")

    def resume(self, ids: Iterable[int]) -> None:
        for item_id in ids:
            item = self.items.get(item_id)
            if item and item.status in (Status.PAUSED, Status.WAITING, Status.SCHEDULED):
                self._set(item, status=Status.QUEUED, stage="Queued", scheduled_at=None)
        self._paused_all = False
        QTimer.singleShot(0, self.tick)

    def start_now(self, ids: Iterable[int]) -> None:
        """Start ignoring schedule and download window."""
        for item_id in ids:
            item = self.items.get(item_id)
            if item and not item.status.is_finished and item_id not in self.tasks:
                self._set(item, status=Status.QUEUED, scheduled_at=None, respect_window=False,
                          priority=max(item.priority, 100))
        self._paused_all = False
        QTimer.singleShot(0, self.tick)

    def schedule(self, ids: Iterable[int], when: datetime) -> None:
        for item_id in ids:
            item = self.items.get(item_id)
            if not item or item.status.is_finished:
                continue
            if item_id in self.tasks:
                self.tasks[item_id].stop("pause")
            self._set(item, status=Status.SCHEDULED, scheduled_at=when,
                      stage=f"Scheduled for {when:%Y-%m-%d %H:%M}")

    def cancel(self, ids: Iterable[int]) -> None:
        for item_id in ids:
            item = self.items.get(item_id)
            if not item or item.status.is_finished:
                continue
            if item_id in self.tasks:
                self.tasks[item_id].stop("cancel")
            else:
                self._set(item, status=Status.CANCELED, stage="Canceled",
                          finished_at=datetime.now())

    def retry(self, ids: Iterable[int]) -> None:
        for item_id in ids:
            item = self.items.get(item_id)
            if item and item.status in (Status.FAILED, Status.CANCELED, Status.COMPLETED):
                self._set(item, status=Status.QUEUED, stage="Queued", error=None, retries=0,
                          progress=0.0, finished_at=None)
        QTimer.singleShot(0, self.tick)

    def remove(self, ids: Iterable[int]) -> None:
        ids = [i for i in ids if i in self.items]
        for item_id in ids:
            if item_id in self.tasks:
                task = self.tasks[item_id]
                task.stop("remove")
        with session_scope() as s:
            for item_id in ids:
                obj = s.get(DownloadItem, item_id)
                if obj:
                    s.delete(obj)
        for item_id in ids:
            self.items.pop(item_id, None)
            self._dirty.discard(item_id)
            self.removed.emit(item_id)

    def clear_finished(self) -> None:
        self.remove([i.id for i in self.finished()])

    def pause_all(self) -> None:
        self._paused_all = True
        self.pause([i.id for i in self.ordered()])

    def resume_all(self) -> None:
        self._paused_all = False
        self.resume([i.id for i in self.ordered()])

    def set_priority(self, ids: Iterable[int], priority: int) -> None:
        for item_id in ids:
            item = self.items.get(item_id)
            if item:
                self._set(item, priority=priority)

    def reorder(self, ordered_ids: list[int]) -> None:
        """Persist a manual order (e.g. from drag-and-drop) for the given ids."""
        for pos, item_id in enumerate(ordered_ids, start=1):
            item = self.items.get(item_id)
            if item and (item.position != pos or item.priority):
                self._set(item, position=pos, priority=0)

    def move(self, ids: list[int], delta: int) -> None:
        order = [i.id for i in self.ordered()]
        moving = [i for i in order if i in ids]
        if not moving:
            return
        rest = [i for i in order if i not in ids]
        first_index = order.index(moving[0])
        new_index = max(0, min(len(rest), first_index + delta))
        self.reorder(rest[:new_index] + moving + rest[new_index:])

    # ------------------------------------------------------------------ scheduling loop

    def blocked_reason(self, item: DownloadItem | None = None, now: datetime | None = None) -> str | None:
        s = self.settings.data
        now = now or datetime.now()
        if s.pause_on_battery and conditions.on_battery():
            return "Paused: running on battery"
        if s.pause_on_metered and conditions.metered.is_metered():
            return "Paused: metered connection"
        if (item is None or item.respect_window) and not rules.in_window(s.window, now):
            nxt = rules.next_window_start(s.window, now)
            return f"Waiting for download window ({nxt:%a %H:%M})" if nxt else \
                "Waiting for download window"
        return None

    def tick(self) -> None:
        s = self.settings.data
        now = datetime.now()

        # 1. Scheduled items whose time has come.
        for item in self.items.values():
            if item.status is Status.SCHEDULED and item.scheduled_at and item.scheduled_at <= now:
                self._set(item, status=Status.QUEUED, scheduled_at=None, stage="Queued")

        # 2. Bandwidth for this moment, split across slots.
        limit_total = rules.current_limit_kbps(s.bandwidth_rules, s.global_limit_kbps, now)
        per_task = rules.per_task_limit(limit_total, s.max_concurrent)

        # 3. Running tasks that must stop (window/conditions) or restart (new rate limit).
        for item_id, task in list(self.tasks.items()):
            if task.stop_reason:
                continue
            item = self.items[item_id]
            if self.blocked_reason(item, now):
                task.stop("wait")
            elif task.limit_kbps != per_task and not task.state.processing:
                task.stop("restart")

        # 4. Start what we can.
        global_block = self.blocked_reason(None, now)
        per_site: dict[str, int] = {}
        for item_id in self.tasks:
            key = site_key(self.items[item_id])
            per_site[key] = per_site.get(key, 0) + 1

        if not self._paused_all:
            for item in self.ordered():
                if len(self.tasks) >= s.max_concurrent:
                    break
                if item.status not in (Status.QUEUED, Status.WAITING) or item.id in self.tasks:
                    continue
                reason = self.blocked_reason(item, now)
                if reason:
                    if item.status is not Status.WAITING or item.stage != reason:
                        self._set(item, status=Status.WAITING, stage=reason)
                    continue
                key = site_key(item)
                if per_site.get(key, 0) >= s.max_per_site:
                    continue
                if not self._has_space(item):
                    continue
                self._start(item, per_task)
                per_site[key] = per_site.get(key, 0) + 1

        self._flush()
        self._emit_stats(limit_total, global_block)
        self._check_queue_finished()

    def _has_space(self, item: DownloadItem) -> bool:
        try:
            free = fsutil.free_space_mb(item.output_root)
        except OSError:
            return True
        if free < self.settings.data.min_free_space_mb:
            reason = f"Waiting: low disk space ({free} MB free)"
            if item.stage != reason:
                self._set(item, status=Status.WAITING, stage=reason)
            return False
        return True

    def _start(self, item: DownloadItem, limit_kbps: int) -> None:
        archive = None
        if item.subscription_id:
            archive = str(paths.archives_dir() / f"subscription-{item.subscription_id}.txt")
        spec = ytdlp.JobSpec(
            url=item.url, output_root=item.output_root, preset=item.preset,
            template_kind=item.template_kind, playlist_title=item.playlist_title,
            playlist_index=item.playlist_index, limit_kbps=limit_kbps, archive_file=archive,
            extra_args=list(item.extra_args or []),
        )
        args = ytdlp.build_args(spec, self.settings.data)
        task = DownloadTask(item.id, args, limit_kbps, self)
        task.updated.connect(self._on_task_updated)
        task.ended.connect(self._on_task_ended)
        self.tasks[item.id] = task
        self._had_work = True
        self._set(item, status=Status.DOWNLOADING, stage="Starting", error=None,
                  started_at=item.started_at or datetime.now())
        log.info("Starting #%d %s", item.id, item.url)
        task.start()

    # ------------------------------------------------------------------ task callbacks

    def _on_task_updated(self, item_id: int) -> None:
        task, item = self.tasks.get(item_id), self.items.get(item_id)
        if not task or not item:
            return
        st = task.state
        info = st.info
        changes: dict = {
            "progress": st.percent,
            "downloaded_bytes": st.downloaded,
            "total_bytes": st.total,
            "stage": st.stage,
            "status": Status.PROCESSING if st.processing else Status.DOWNLOADING,
        }
        if info:
            changes.update(
                title=item.title or info.get("title"),
                uploader=item.uploader or info.get("uploader") or info.get("channel"),
                extractor=item.extractor or info.get("extractor_key"),
                video_id=item.video_id or info.get("id"),
                duration=item.duration or info.get("duration"),
                thumbnail_url=item.thumbnail_url or info.get("thumbnail"),
            )
        # Changes to progress are persisted on the next tick, not per line of output.
        for key, value in changes.items():
            setattr(item, key, value)
        self._dirty.add(item_id)
        self.changed.emit(item_id)

    def _on_task_ended(self, item_id: int, outcome: str) -> None:
        task = self.tasks.pop(item_id, None)
        item = self.items.get(item_id)
        if task is None:
            return
        task.deleteLater()
        if item is None:  # removed while running
            if task.stop_reason == "remove":
                task.cleanup_partials()
            return

        now = datetime.now()
        st = task.state
        if outcome == "completed":
            info = st.done_info or {}
            self._set(item, status=Status.COMPLETED, progress=100.0, stage="Completed",
                      filepath=info.get("filepath") or item.filepath, finished_at=now,
                      error=st.last_error, title=item.title or info.get("title"))
            log.info("Completed #%d -> %s", item_id, item.filepath)
            if st.done_info:
                self.completed.emit(item_id, st.done_info)
        elif outcome == "stopped":
            reason = task.stop_reason
            if reason == "cancel":
                task.cleanup_partials()
                self._set(item, status=Status.CANCELED, stage="Canceled", finished_at=now)
            elif reason == "pause":
                if item.status is not Status.SCHEDULED:
                    self._set(item, status=Status.PAUSED, stage="Paused")
            elif reason == "wait":
                self._set(item, status=Status.WAITING, stage=self.blocked_reason(item) or "Waiting")
            else:  # restart / shutdown: resume on the next tick or next launch
                self._set(item, status=Status.QUEUED, stage="Queued")
        else:
            error = st.last_error or "Unknown error"
            max_retries = self.settings.data.max_retries
            if ytdlp.is_retryable(error) and item.retries < max_retries:
                delay = RETRY_BASE_SECONDS * (2 ** item.retries)
                when = now + timedelta(seconds=delay)
                self._set(item, status=Status.SCHEDULED, scheduled_at=when, error=error,
                          retries=item.retries + 1,
                          stage=f"Retry {item.retries + 1}/{max_retries} at {when:%H:%M:%S}")
                log.warning("#%d failed (%s); retrying in %ss", item_id, error, delay)
            else:
                self._set(item, status=Status.FAILED, stage="Failed", error=error,
                          finished_at=now)
                log.error("#%d failed permanently: %s", item_id, error)
                self.failed.emit(item_id, error)
        self._flush()
        QTimer.singleShot(0, self.tick)

    # ------------------------------------------------------------------ persistence & stats

    def _flush(self) -> None:
        if not self._dirty:
            return
        dirty, self._dirty = self._dirty, set()
        with session_scope() as s:
            for item_id in dirty:
                item = self.items.get(item_id)
                if item is not None:
                    s.merge(item)

    def _emit_stats(self, limit_kbps: int, blocked: str | None) -> None:
        speed = sum((t.state.speed or 0) for t in self.tasks.values() if not t.state.processing)
        queued = sum(1 for i in self.items.values()
                     if i.status in (Status.QUEUED, Status.WAITING, Status.SCHEDULED))
        self.stats.emit({
            "active": len(self.tasks),
            "queued": queued,
            "speed": speed,
            "limit_kbps": limit_kbps,
            "blocked_reason": blocked,
            "paused_all": self._paused_all,
        })

    def _check_queue_finished(self) -> None:
        if not self._had_work or self.tasks:
            return
        pending = any(i.status in (Status.QUEUED, Status.DOWNLOADING, Status.PROCESSING,
                                   Status.SCHEDULED)
                      for i in self.items.values())
        if not pending:
            self._had_work = False
            self.queue_finished.emit()
