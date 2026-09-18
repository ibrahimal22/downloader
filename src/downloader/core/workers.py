"""Run blocking callables on the Qt thread pool and deliver results on the UI thread."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

log = logging.getLogger(__name__)


class _Signals(QObject):
    done = Signal(object)
    error = Signal(object)
    progress = Signal(object)


class _Job(QRunnable):
    def __init__(self, fn: Callable[..., Any], with_progress: bool) -> None:
        super().__init__()
        self.fn = fn
        self.with_progress = with_progress
        self.signals = _Signals()

    def run(self) -> None:
        try:
            if self.with_progress:
                result = self.fn(self.signals.progress.emit)
            else:
                result = self.fn()
        except Exception as exc:  # noqa: BLE001 - surfaced to the caller
            log.debug("Background job failed", exc_info=True)
            self.signals.error.emit(exc)
        else:
            self.signals.done.emit(result)


_live: set[_Job] = set()  # keep signal objects alive until delivery


def run_async(
    fn: Callable[..., Any],
    on_done: Callable[[Any], None] | None = None,
    on_error: Callable[[Exception], None] | None = None,
    on_progress: Callable[[Any], None] | None = None,
) -> None:
    """Run fn() in the pool. If on_progress is given, fn receives a progress callback."""
    job = _Job(fn, with_progress=on_progress is not None)
    _live.add(job)

    def finish(_=None) -> None:
        _live.discard(job)

    if on_done:
        job.signals.done.connect(on_done)
    if on_error:
        job.signals.error.connect(on_error)
    else:
        job.signals.error.connect(lambda e: log.error("Background job failed: %s", e))
    if on_progress:
        job.signals.progress.connect(on_progress)
    job.signals.done.connect(finish)
    job.signals.error.connect(finish)
    job.setAutoDelete(False)
    QThreadPool.globalInstance().start(job)
