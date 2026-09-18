"""One running yt-dlp process, driven by QProcess so the UI thread never blocks."""

from __future__ import annotations

import glob
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import psutil
from PySide6.QtCore import QObject, QProcess, Signal

from downloader.core import binaries
from downloader.core.ytdlp import (
    DoneEvent,
    InfoEvent,
    MessageEvent,
    PostprocessEvent,
    ProgressEvent,
    friendly_error,
    parse_line,
)

log = logging.getLogger(__name__)

_PP_LABELS = {
    "Merger": "Merging video and audio",
    "FFmpegMerger": "Merging video and audio",
    "ExtractAudio": "Converting audio",
    "FFmpegExtractAudio": "Converting audio",
    "EmbedThumbnail": "Embedding thumbnail",
    "Metadata": "Writing metadata",
    "FFmpegMetadata": "Writing metadata",
    "EmbedSubtitle": "Embedding subtitles",
    "SponsorBlock": "Processing SponsorBlock",
    "ModifyChapters": "Removing segments",
    "ThumbnailsConvertor": "Converting thumbnail",
    "SubtitlesConvertor": "Converting subtitles",
    "VideoConvertor": "Converting video",
    "VideoRemuxer": "Remuxing",
    "MoveFiles": "Finishing",
}


@dataclass
class TaskState:
    percent: float = 0.0
    downloaded: int = 0
    total: int | None = None
    speed: float | None = None
    eta: int | None = None
    stage: str = "Starting"
    processing: bool = False
    info: dict = field(default_factory=dict)
    done_info: dict | None = None
    last_error: str | None = None
    warnings: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)  # temp filenames seen, for cleanup
    log_tail: list[str] = field(default_factory=list)


class DownloadTask(QObject):
    """Emits `updated` as progress changes and `ended(outcome)` exactly once.

    outcome: "completed" | "failed" | "stopped" (stopped via stop(); see `stop_reason`).
    """

    updated = Signal(int)
    ended = Signal(int, str)

    LOG_TAIL = 60

    def __init__(self, item_id: int, args: list[str], limit_kbps: int, parent=None) -> None:
        super().__init__(parent)
        self.item_id = item_id
        self.args = args
        self.limit_kbps = limit_kbps
        self.state = TaskState()
        self.stop_reason: str | None = None
        self._buffer = b""
        self._parts = 1
        self._finished_parts_bytes = 0
        self._current_file: str | None = None
        self._ended = False
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._on_output)
        self.process.finished.connect(self._on_finished)
        self.process.errorOccurred.connect(self._on_error)

    # ------------------------------------------------------------------ control

    def start(self) -> None:
        exe = binaries.find("yt-dlp")
        if exe is None:
            self.state.last_error = "yt-dlp is not installed"
            self._end("failed")
            return
        self.process.setProgram(str(exe))
        self.process.setArguments(self.args)
        self.process.start()

    def stop(self, reason: str) -> None:
        """Stop the process; reason is e.g. 'pause', 'cancel', 'wait', 'restart'."""
        self.stop_reason = reason
        if self.process.state() == QProcess.ProcessState.NotRunning:
            self._end("stopped")
            return
        self._kill_tree()

    def _kill_tree(self) -> None:
        pid = self.process.processId()
        if pid:
            try:
                for child in psutil.Process(pid).children(recursive=True):
                    child.kill()
            except psutil.Error:
                pass
        self.process.kill()

    @property
    def running(self) -> bool:
        return not self._ended

    # ------------------------------------------------------------------ parsing

    def _on_output(self) -> None:
        self._buffer += bytes(self.process.readAllStandardOutput().data())
        *lines, self._buffer = self._buffer.split(b"\n")
        changed = False
        for raw in lines:
            changed |= self._handle(raw.decode("utf-8", errors="replace"))
        if changed:
            self.updated.emit(self.item_id)

    def _handle(self, line: str) -> bool:
        event = parse_line(line)
        if event is None:
            return False
        st = self.state
        if isinstance(event, ProgressEvent):
            return self._handle_progress(event)
        if isinstance(event, PostprocessEvent):
            if event.status == "started":
                st.processing = True
                st.stage = _PP_LABELS.get(event.postprocessor, event.postprocessor)
                st.speed = st.eta = None
                return True
            return False
        if isinstance(event, InfoEvent):
            st.info = event.info
            fmt = str(event.info.get("format_id") or "")
            self._parts = fmt.count("+") + 1 if fmt else 1
            return True
        if isinstance(event, DoneEvent):
            st.done_info = event.info
            return False
        assert isinstance(event, MessageEvent)
        st.log_tail.append(line)
        del st.log_tail[: -self.LOG_TAIL]
        if event.level == "error":
            st.last_error = event.text
        elif event.level == "warning":
            st.warnings.append(event.text)
        return False

    def _handle_progress(self, ev: ProgressEvent) -> bool:
        st = self.state
        if ev.filename and ev.filename != self._current_file:
            if self._current_file is not None:
                self._finished_parts_bytes += st.total or 0
            self._current_file = ev.filename
            if ev.filename not in st.files:
                st.files.append(ev.filename)
        part_index = max(0, len(st.files) - 1)
        part_frac = (ev.percent or 0.0) / 100.0
        if ev.status == "finished":
            part_frac = 1.0
        parts = max(self._parts, len(st.files))
        st.percent = min(100.0, (part_index + part_frac) * 100.0 / parts)
        st.downloaded = self._finished_parts_bytes + ev.downloaded
        st.total = (self._finished_parts_bytes + ev.total) if ev.total else None
        st.speed = ev.speed
        st.eta = ev.eta
        st.processing = False
        if parts > 1:
            kind = "video" if part_index == 0 else "audio"
            st.stage = f"Downloading {kind} ({part_index + 1}/{parts})"
        else:
            st.stage = "Downloading"
        return True

    # ------------------------------------------------------------------ completion

    def _on_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self.state.last_error = f"Could not start yt-dlp: {self.process.errorString()}"
            self._end("failed")

    def _on_finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        if self._buffer:
            self._handle(self._buffer.decode("utf-8", errors="replace"))
            self._buffer = b""
        if self.stop_reason:
            self._end("stopped")
        elif exit_code == 0 and self.state.done_info:
            self.state.percent = 100.0
            self._end("completed")
        elif exit_code == 0 and not self.state.last_error:
            # yt-dlp exited cleanly without downloading: archive hit or a date/match filter.
            self.state.last_error = "Skipped by yt-dlp (already archived or filtered out)"
            self._end("completed")
        else:
            if not self.state.last_error:
                self.state.last_error = f"yt-dlp exited with code {exit_code}"
            self.state.last_error = friendly_error(self.state.last_error)
            self._end("failed")

    def _end(self, outcome: str) -> None:
        if self._ended:
            return
        self._ended = True
        self.ended.emit(self.item_id, outcome)

    def cleanup_partials(self) -> None:
        """Delete partial files for a canceled download."""
        for name in self.state.files:
            for path in glob.glob(glob.escape(name) + "*"):
                try:
                    os.remove(path)
                except OSError as exc:
                    log.debug("Could not remove %s: %s", path, exc)
            parent = Path(name).parent
            if parent.name == ".incomplete":
                try:
                    parent.rmdir()  # only succeeds when empty
                except OSError:
                    pass
