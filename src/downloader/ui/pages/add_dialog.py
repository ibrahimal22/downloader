"""Add downloads: paste URLs -> fetch info -> pick entries/quality/folder/schedule."""

from __future__ import annotations

import re

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from downloader.config.settings import SettingsStore
from downloader.core import presets, ytdlp
from downloader.core.download_manager import DownloadManager, NewDownload
from downloader.core.workers import run_async
from downloader.subscriptions.sync import SubscriptionService, append_archive
from downloader.ui import fmt, theme
from downloader.ui.widgets.common import hbox, primary_button

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
EntryRole = Qt.ItemDataRole.UserRole + 1
ResultRole = Qt.ItemDataRole.UserRole + 2

PRIORITIES = {"Normal": 0, "High": 10, "Low": -10}


def extract_urls(text: str) -> list[str]:
    seen: dict[str, None] = {}
    for match in URL_RE.findall(text or ""):
        seen.setdefault(match.rstrip(").,;]"), None)
    return list(seen)


def preset_combo(selected: str) -> QComboBox:
    combo = QComboBox()
    for preset in presets.PRESETS.values():
        combo.addItem(preset.label, preset.id)
    index = combo.findData(selected)
    combo.setCurrentIndex(max(0, index))
    return combo


class AddDialog(QDialog):
    def __init__(self, settings: SettingsStore, manager: DownloadManager,
                 subscriptions: SubscriptionService, initial_text: str = "",
                 parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.manager = manager
        self.subscriptions = subscriptions
        self._pending = 0
        self._probed: set[str] = set()
        self.setWindowTitle("Add downloads")
        self.resize(900, 680)

        self.urls = QPlainTextEdit()
        self.urls.setPlaceholderText("Paste one or more links (videos, playlists, channels)…")
        self.urls.setFixedHeight(90)
        self.fetch_btn = QPushButton("Fetch info")
        self.fetch_btn.clicked.connect(self.fetch)
        import_btn = QPushButton("Import .txt…")
        import_btn.clicked.connect(self.import_txt)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Title", "Duration", "Channel"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.setUniformRowHeights(True)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.status = QLabel("")
        self.status.setObjectName("Muted")

        s = settings.data
        self.preset = preset_combo(s.default_preset)
        self.folder = QLineEdit(s.download_dir)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        self.playlist_folders = QCheckBox("Put playlist videos in their own numbered folder")
        self.playlist_folders.setChecked(True)
        self.priority = QComboBox()
        self.priority.addItems(list(PRIORITIES))

        self.start_now = QRadioButton("Start when a slot is free")
        self.start_now.setChecked(True)
        self.start_at = QRadioButton("Schedule for")
        self.when = QDateTimeEdit(QDateTime.currentDateTime().addSecs(3600))
        self.when.setCalendarPopup(True)
        self.when.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.when.setEnabled(False)
        self.start_at.toggled.connect(self.when.setEnabled)
        self.respect_window = QCheckBox("Only download inside the scheduled download window")
        self.respect_window.setChecked(True)
        self.subscribe = QCheckBox("Also subscribe to playlists/channels (auto-download new videos)")
        self.subscribe.setVisible(False)

        form = QFormLayout()
        form.addRow("Quality", self.preset)
        form.addRow("Save to", hbox(self.folder, browse))
        form.addRow("Priority", self.priority)
        form.addRow("Start", hbox(self.start_now, self.start_at, self.when, None))
        form.addRow("", self.respect_window)
        form.addRow("", self.playlist_folders)
        form.addRow("", self.subscribe)

        self.add_btn = primary_button("Download", self.accept_downloads, "download")
        self.add_btn.setEnabled(False)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        select_all = QPushButton("Select all")
        select_all.clicked.connect(lambda: self._check_all(True))
        select_none = QPushButton("Select none")
        select_none.clicked.connect(lambda: self._check_all(False))

        lay = QVBoxLayout(self)
        lay.addWidget(self.urls)
        lay.addLayout(hbox(self.fetch_btn, import_btn, self.status, None))
        lay.addWidget(self.tree, 1)
        lay.addLayout(hbox(select_all, select_none, None))
        lay.addLayout(form)
        lay.addLayout(hbox(None, cancel, self.add_btn))

        text = initial_text or QGuiApplication.clipboard().text()
        urls = extract_urls(text)
        if urls:
            self.urls.setPlainText("\n".join(urls))
            self.fetch()

    # -------------------------------------------------------------- fetching

    def import_txt(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import links", "", "Text files (*.txt);;All (*)")
        if path:
            with open(path, encoding="utf-8", errors="replace") as fh:
                urls = extract_urls(fh.read())
            existing = self.urls.toPlainText().strip()
            self.urls.setPlainText("\n".join(filter(None, [existing, *urls])))
            self.fetch()

    def fetch(self) -> None:
        urls = [u for u in extract_urls(self.urls.toPlainText()) if u not in self._probed]
        if not urls:
            return
        settings = self.settings.data
        for url in urls:
            self._probed.add(url)
            self._pending += 1
            placeholder = QTreeWidgetItem([f"Fetching {url} …", "", ""])
            placeholder.setData(0, ResultRole, url)
            self.tree.addTopLevelItem(placeholder)
            run_async(
                lambda u=url: ytdlp.probe(u, settings),
                on_done=lambda result, u=url: self._on_probed(u, result),
                on_error=lambda exc, u=url: self._on_probe_error(u, exc),
            )
        self._update_status()

    def _placeholder(self, url: str) -> QTreeWidgetItem | None:
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.data(0, ResultRole) == url:
                return item
        return None

    def _on_probed(self, url: str, result: ytdlp.ProbeResult) -> None:
        self._pending -= 1
        top = self._placeholder(url)
        if top is None:  # dialog was cleared
            return
        self.tree.blockSignals(True)
        top.setData(0, ResultRole, result)
        top.setFlags(top.flags() | Qt.ItemFlag.ItemIsUserCheckable |
                     Qt.ItemFlag.ItemIsAutoTristate)
        if result.is_playlist:
            top.setText(0, f"{result.title}  ({len(result.entries)} videos)")
            top.setText(2, result.uploader or "")
            for entry in result.entries:
                child = QTreeWidgetItem([f"{entry.index}. {entry.title}",
                                         fmt.duration(entry.duration), entry.uploader or ""])
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                child.setCheckState(0, Qt.CheckState.Checked)
                child.setData(0, EntryRole, entry)
                top.addChild(child)
            top.setCheckState(0, Qt.CheckState.Checked)
            top.setExpanded(len(result.entries) <= 50)
            self.subscribe.setVisible(True)
        else:
            top.setText(0, result.title)
            top.setText(1, fmt.duration(result.duration))
            top.setText(2, result.uploader or "")
            top.setCheckState(0, Qt.CheckState.Checked)
            if result.heights:
                top.setToolTip(0, "Available: " + ", ".join(f"{h}p" for h in result.heights))
        self.tree.blockSignals(False)
        self._update_status()

    def _on_probe_error(self, url: str, exc: Exception) -> None:
        self._pending -= 1
        top = self._placeholder(url)
        if top is not None:
            top.setText(0, f"{url} — {exc}")
            top.setForeground(0, theme.color("danger"))
            top.setToolTip(0, str(exc))
        self._update_status()

    def _update_status(self) -> None:
        count = len(self._collect_entries())
        if self._pending:
            self.status.setText(f"Fetching info for {self._pending} link(s)…")
        else:
            self.status.setText(f"{count} video(s) selected")
        self.add_btn.setEnabled(count > 0)
        self.add_btn.setText(f"Download {count}" if count else "Download")

    def _on_item_changed(self, *_args) -> None:
        self._update_status()

    def _check_all(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if isinstance(item.data(0, ResultRole), ytdlp.ProbeResult):
                item.setCheckState(0, state)

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Save to", self.folder.text())
        if folder:
            self.folder.setText(folder)

    # -------------------------------------------------------------- accept

    def _collect_entries(self) -> list[tuple[ytdlp.ProbeResult, ytdlp.ProbeEntry | None]]:
        out = []
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            result = top.data(0, ResultRole)
            if not isinstance(result, ytdlp.ProbeResult):
                continue
            if result.is_playlist:
                for j in range(top.childCount()):
                    child = top.child(j)
                    if child.checkState(0) == Qt.CheckState.Checked:
                        out.append((result, child.data(0, EntryRole)))
            elif top.checkState(0) == Qt.CheckState.Checked:
                out.append((result, None))
        return out

    def accept_downloads(self) -> None:
        folder = self.folder.text().strip()
        if not folder:
            QMessageBox.warning(self, "Add downloads", "Choose a folder to save to.")
            return
        preset = self.preset.currentData()
        scheduled = self.when.dateTime().toPython() if self.start_at.isChecked() else None
        priority = PRIORITIES[self.priority.currentText()]
        respect = self.respect_window.isChecked()
        numbered = self.playlist_folders.isChecked()

        new: list[NewDownload] = []
        for result, entry in self._collect_entries():
            common = dict(output_root=folder, preset=preset, scheduled_at=scheduled,
                          priority=priority, respect_window=respect,
                          extractor=(result.extractor or "").replace("Tab", "") or None)
            if entry is None:
                new.append(NewDownload(
                    url=result.url, title=result.title, uploader=result.uploader,
                    video_id=result.video_id, duration=result.duration,
                    thumbnail_url=result.thumbnail, **common))
            else:
                new.append(NewDownload(
                    url=entry.url, title=entry.title, uploader=entry.uploader or result.uploader,
                    video_id=entry.id, duration=entry.duration, thumbnail_url=entry.thumbnail,
                    playlist_title=result.title if numbered else None,
                    playlist_index=entry.index if numbered else None,
                    template_kind="playlist" if numbered else "single", **common))

        added, skipped = self.manager.add(new)

        if self.subscribe.isVisible() and self.subscribe.isChecked():
            for i in range(self.tree.topLevelItemCount()):
                result = self.tree.topLevelItem(i).data(0, ResultRole)
                if isinstance(result, ytdlp.ProbeResult) and result.is_playlist:
                    sub = self.subscriptions.create(
                        result.title, result.url, preset=preset, output_root=folder,
                        filters={"numbering": numbered})
                    # Everything listed now was either queued or deliberately unchecked, so
                    # future checks only pick up videos published after this point.
                    append_archive(sub.id, result.extractor or "generic",
                                   [e.id for e in result.entries if e.id])
                    self.subscriptions.update(sub.id, last_checked=QDateTime.currentDateTime()
                                              .toPython())

        if skipped:
            QMessageBox.information(
                self, "Duplicates skipped",
                f"Queued {len(added)} download(s). Skipped {skipped} already in your library "
                "or queue.\n\n(Change this in Settings → Downloads → Skip duplicates.)")
        self.accept()
