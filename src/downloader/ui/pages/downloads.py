"""Downloads (active queue) and History (finished) pages."""

from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtCore import QDateTime, Qt, Signal
from PySide6.QtGui import QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from downloader.core import fsutil
from downloader.core.download_manager import DownloadManager
from downloader.db.models import DownloadItem, Status
from downloader.ui.widgets.common import action, hbox, page_header, primary_button, tool_button
from downloader.ui.widgets.downloads_model import DownloadsModel, ProgressDelegate

ACTIVE_FILTERS = {
    "All": None,
    "Downloading": {Status.DOWNLOADING, Status.PROCESSING},
    "Queued": {Status.QUEUED},
    "Waiting": {Status.WAITING},
    "Scheduled": {Status.SCHEDULED},
    "Paused": {Status.PAUSED},
}

HISTORY_FILTERS = {
    "All": None,
    "Completed": {Status.COMPLETED},
    "Failed": {Status.FAILED},
    "Canceled": {Status.CANCELED},
}


class ScheduleDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Schedule download")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Start at:"))
        self.when = QDateTimeEdit(QDateTime.currentDateTime().addSecs(3600))
        self.when.setCalendarPopup(True)
        self.when.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.when.setMinimumDateTime(QDateTime.currentDateTime())
        lay.addWidget(self.when)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def value(self) -> datetime:
        return self.when.dateTime().toPython()


class _BaseDownloadsPage(QWidget):
    add_requested = Signal()

    columns: list[str] = []
    filters: dict[str, set[Status] | None] = {}

    def __init__(self, manager: DownloadManager, parent=None) -> None:
        super().__init__(parent)
        self.manager = manager
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(list(self.filters))
        self.filter_combo.currentTextChanged.connect(lambda _t: self.model.refresh())

        self.model = DownloadsModel(manager, self.columns, self._source, self)
        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.view.setAlternatingRowColors(True)
        self.view.setShowGrid(False)
        self.view.setWordWrap(False)
        self.view.verticalHeader().hide()
        self.view.verticalHeader().setDefaultSectionSize(36)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._context_menu)
        self.view.doubleClicked.connect(lambda idx: self._on_double_click(idx.row()))
        header = self.view.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setHighlightSections(False)
        if "progress" in self.columns:
            col = self.columns.index("progress")
            self.view.setItemDelegateForColumn(col, ProgressDelegate(self.view))
            self.view.setColumnWidth(col, 170)
        if "status" in self.columns:
            self.view.setColumnWidth(self.columns.index("status"), 240)
        self.view.selectionModel().selectionChanged.connect(lambda *_: self._update_buttons())

        self.empty = QLabel(self._empty_text())
        self.empty.setObjectName("Muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.model.rowsInserted.connect(self._update_empty)
        self.model.rowsRemoved.connect(self._update_empty)
        self.model.modelReset.connect(self._update_empty)

    def _source(self) -> list[DownloadItem]:
        raise NotImplementedError

    def _empty_text(self) -> str:
        return ""

    def _allowed(self) -> set[Status] | None:
        return self.filters.get(self.filter_combo.currentText())

    def _update_empty(self, *_args) -> None:
        empty = self.model.rowCount() == 0
        self.empty.setVisible(empty)
        self.view.setVisible(not empty)

    def selected_items(self) -> list[DownloadItem]:
        rows = sorted({i.row() for i in self.view.selectionModel().selectedRows()})
        return [item for r in rows if (item := self.model.item_at(r)) is not None]

    def selected_ids(self) -> list[int]:
        return [i.id for i in self.selected_items()]

    def _update_buttons(self) -> None:
        pass

    def _context_menu(self, pos) -> None:
        raise NotImplementedError

    def _on_double_click(self, row: int) -> None:
        pass

    def _copy_urls(self) -> None:
        QGuiApplication.clipboard().setText("\n".join(i.url for i in self.selected_items()))


class DownloadsPage(_BaseDownloadsPage):
    columns = ["title", "status", "progress", "size", "speed", "eta", "preset"]
    filters = ACTIVE_FILTERS

    def __init__(self, manager: DownloadManager, parent=None) -> None:
        super().__init__(manager, parent)
        self.btn_resume = tool_button("play", "Resume / start selected", self.resume)
        self.btn_pause = tool_button("pause", "Pause selected", self.pause)
        self.btn_now = tool_button("lightning-bolt-outline", "Start now (ignore schedule)",
                                   self.start_now)
        self.btn_schedule = tool_button("calendar-clock", "Schedule…", self.schedule)
        self.btn_up = tool_button("arrow-up", "Move up", lambda: self.move(-1))
        self.btn_down = tool_button("arrow-down", "Move down", lambda: self.move(1))
        self.btn_top = tool_button("arrow-collapse-up", "Move to top", lambda: self.move(-10_000))
        self.btn_cancel = tool_button("close", "Cancel selected", self.cancel)
        self.btn_remove = tool_button("delete-outline", "Remove from list", self.remove)
        self.btn_pause_all = tool_button("pause-circle-outline", "Pause all", manager.pause_all,
                                         "Pause all")
        self.btn_resume_all = tool_button("play-circle-outline", "Resume all", manager.resume_all,
                                          "Resume all")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.addLayout(hbox(
            page_header("Downloads", "Active queue. Drag in URLs, or paste with Ctrl+V."),
            None,
            primary_button("Add downloads", self.add_requested.emit, "plus"),
        ))
        layout.addLayout(hbox(
            self.btn_resume, self.btn_pause, self.btn_now, self.btn_schedule, self.btn_cancel,
            self.btn_remove, QLabel("  "), self.btn_top, self.btn_up, self.btn_down, None,
            QLabel("Show:"), self.filter_combo, self.btn_pause_all, self.btn_resume_all,
        ))
        layout.addWidget(self.view, 1)
        layout.addWidget(self.empty, 1)

        for seq, slot in ((QKeySequence.StandardKey.Delete, self.remove),
                          ("Space", self.toggle_pause)):
            act = action(self, "", slot, shortcut=seq)
            act.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            self.view.addAction(act)
        self._update_buttons()
        self._update_empty()

    def _source(self) -> list[DownloadItem]:
        allowed = self._allowed()
        return [i for i in self.manager.ordered() if allowed is None or i.status in allowed]

    def _empty_text(self) -> str:
        return "No active downloads.\nClick “Add downloads” or paste a link (Ctrl+V)."

    def _update_buttons(self) -> None:
        has = bool(self.selected_ids())
        for btn in (self.btn_resume, self.btn_pause, self.btn_now, self.btn_schedule,
                    self.btn_cancel, self.btn_remove, self.btn_up, self.btn_down, self.btn_top):
            btn.setEnabled(has)

    # -------------------------------------------------------------- actions

    def resume(self) -> None:
        self.manager.resume(self.selected_ids())

    def pause(self) -> None:
        self.manager.pause(self.selected_ids())

    def toggle_pause(self) -> None:
        items = self.selected_items()
        if any(i.status is Status.PAUSED for i in items):
            self.resume()
        else:
            self.pause()

    def start_now(self) -> None:
        self.manager.start_now(self.selected_ids())

    def schedule(self) -> None:
        ids = self.selected_ids()
        if not ids:
            return
        dlg = ScheduleDialog(self)
        if dlg.exec():
            self.manager.schedule(ids, dlg.value())

    def cancel(self) -> None:
        ids = self.selected_ids()
        if ids and QMessageBox.question(
            self, "Cancel downloads", f"Cancel {len(ids)} download(s) and delete partial files?"
        ) == QMessageBox.StandardButton.Yes:
            self.manager.cancel(ids)

    def remove(self) -> None:
        ids = self.selected_ids()
        if ids and QMessageBox.question(
            self, "Remove downloads", f"Remove {len(ids)} item(s) from the queue?"
        ) == QMessageBox.StandardButton.Yes:
            self.manager.remove(ids)

    def move(self, delta: int) -> None:
        self.manager.move(self.selected_ids(), delta)

    def _context_menu(self, pos) -> None:
        if not self.selected_ids():
            return
        menu = QMenu(self)
        menu.addAction(action(menu, "Resume / start", self.resume, "play"))
        menu.addAction(action(menu, "Pause", self.pause, "pause"))
        menu.addAction(action(menu, "Start now", self.start_now, "lightning-bolt-outline"))
        menu.addAction(action(menu, "Schedule…", self.schedule, "calendar-clock"))
        prio = menu.addMenu("Priority")
        for label, value in (("High", 10), ("Normal", 0), ("Low", -10)):
            prio.addAction(action(prio, label, lambda v=value: self.manager.set_priority(
                self.selected_ids(), v)))
        menu.addSeparator()
        menu.addAction(action(menu, "Copy URL", self._copy_urls, "content-copy"))
        menu.addAction(action(menu, "Open output folder", self._open_folder, "folder-outline"))
        menu.addSeparator()
        menu.addAction(action(menu, "Cancel", self.cancel, "close"))
        menu.addAction(action(menu, "Remove", self.remove, "delete-outline"))
        menu.exec(self.view.viewport().mapToGlobal(pos))

    def _open_folder(self) -> None:
        items = self.selected_items()
        if items:
            fsutil.open_path(items[0].output_root)


class HistoryPage(_BaseDownloadsPage):
    columns = ["title", "status", "uploader", "preset", "size", "finished"]
    filters = HISTORY_FILTERS

    def __init__(self, manager: DownloadManager, parent=None) -> None:
        super().__init__(manager, parent)
        self.btn_open = tool_button("play-circle-outline", "Open file", self.open_file)
        self.btn_folder = tool_button("folder-outline", "Show in folder", self.show_in_folder)
        self.btn_retry = tool_button("refresh", "Retry / download again", self.retry)
        self.btn_remove = tool_button("delete-outline", "Remove from history", self.remove)
        clear_failed = tool_button("broom", "Clear failed & canceled", self.clear_failed,
                                   "Clear failed")
        clear = tool_button("delete-sweep-outline", "Clear history", self.clear_all, "Clear all")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.addWidget(page_header("History", "Finished, failed and canceled downloads."))
        layout.addLayout(hbox(self.btn_open, self.btn_folder, self.btn_retry, self.btn_remove,
                              None, QLabel("Show:"), self.filter_combo, clear_failed, clear))
        layout.addWidget(self.view, 1)
        layout.addWidget(self.empty, 1)
        self._update_buttons()
        self._update_empty()

    def _source(self) -> list[DownloadItem]:
        allowed = self._allowed()
        cutoff = datetime.now() - timedelta(days=365)
        return [i for i in self.manager.finished()
                if (allowed is None or i.status in allowed)
                and (i.finished_at is None or i.finished_at > cutoff)]

    def _empty_text(self) -> str:
        return "Nothing here yet. Finished downloads will appear here."

    def _update_buttons(self) -> None:
        items = self.selected_items()
        has_file = any(i.status is Status.COMPLETED and i.filepath for i in items)
        self.btn_open.setEnabled(has_file)
        self.btn_folder.setEnabled(bool(items))
        self.btn_retry.setEnabled(bool(items))
        self.btn_remove.setEnabled(bool(items))

    def _on_double_click(self, row: int) -> None:
        item = self.model.item_at(row)
        if item and item.status is Status.FAILED and item.error:
            QMessageBox.information(self, "Download failed", item.error)
        else:
            self.open_file()

    def open_file(self) -> None:
        for item in self.selected_items()[:1]:
            if item.filepath:
                fsutil.open_path(item.filepath)

    def show_in_folder(self) -> None:
        for item in self.selected_items()[:1]:
            fsutil.reveal_in_folder(item.filepath or item.output_root)

    def retry(self) -> None:
        self.manager.retry(self.selected_ids())

    def remove(self) -> None:
        self.manager.remove(self.selected_ids())

    def clear_failed(self) -> None:
        self.manager.remove([i.id for i in self.manager.finished()
                             if i.status in (Status.FAILED, Status.CANCELED)])

    def clear_all(self) -> None:
        if QMessageBox.question(self, "Clear history",
                                "Remove all finished downloads from history? "
                                "Files on disk and your library are not affected."
                                ) == QMessageBox.StandardButton.Yes:
            self.manager.clear_finished()

    def _context_menu(self, pos) -> None:
        items = self.selected_items()
        if not items:
            return
        menu = QMenu(self)
        menu.addAction(action(menu, "Open file", self.open_file, "play-circle-outline"))
        menu.addAction(action(menu, "Show in folder", self.show_in_folder, "folder-outline"))
        menu.addAction(action(menu, "Download again", self.retry, "refresh"))
        menu.addAction(action(menu, "Copy URL", self._copy_urls, "content-copy"))
        if items[0].error:
            menu.addAction(action(menu, "Show error", lambda: QMessageBox.information(
                self, "Error details", items[0].error or ""), "alert-circle-outline"))
        menu.addSeparator()
        menu.addAction(action(menu, "Remove from history", self.remove, "delete-outline"))
        menu.exec(self.view.viewport().mapToGlobal(pos))
