"""Subscriptions: playlists/channels that are checked on a schedule for new videos."""

from __future__ import annotations

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from downloader.config.settings import SettingsStore
from downloader.core import fsutil, presets
from downloader.db.models import Subscription
from downloader.subscriptions.sync import SubscriptionService
from downloader.ui import theme
from downloader.ui.pages.add_dialog import extract_urls, preset_combo
from downloader.ui.widgets.common import action, hbox, page_header, primary_button, tool_button

IdRole = Qt.ItemDataRole.UserRole + 1

INTERVALS = [("Every 30 minutes", 30), ("Every hour", 60), ("Every 3 hours", 180),
             ("Every 6 hours", 360), ("Every 12 hours", 720), ("Daily", 1440),
             ("Weekly", 10080)]


def interval_label(minutes: int) -> str:
    for label, value in INTERVALS:
        if value == minutes:
            return label
    return f"Every {minutes} min"


class SubscriptionDialog(QDialog):
    def __init__(self, settings: SettingsStore, sub: Subscription | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit subscription" if sub else "New subscription")
        self.resize(560, 0)
        filters = (sub.filters if sub else {}) or {}
        s = settings.data

        self.name = QLineEdit(sub.name if sub else "")
        self.url = QLineEdit(sub.url if sub else "")
        self.url.setPlaceholderText("https://www.youtube.com/@channel or a playlist URL")
        if not sub:
            clip =extract_urls(QGuiApplication.clipboard().text())
            if clip:
                self.url.setText(clip[0])
        self.preset = preset_combo(sub.preset if sub else s.default_preset)
        self.folder = QLineEdit(sub.output_root if sub else "")
        self.folder.setPlaceholderText(f"{s.download_dir}/<name>")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        self.interval = QComboBox()
        for label, value in INTERVALS:
            self.interval.addItem(label, value)
        self.interval.setCurrentIndex(max(0, self.interval.findData(
            sub.interval_minutes if sub else 360)))
        self.from_now_on = QCheckBox("Only new videos from now on (skip the existing back-catalog)")
        self.from_now_on.setChecked(sub.from_now_on if sub else False)
        self.from_now_on.setEnabled(sub is None or sub.last_checked is None)
        self.keep_last = QSpinBox()
        self.keep_last.setRange(0, 10000)
        self.keep_last.setSpecialValueText("Keep all")
        self.keep_last.setValue(sub.keep_last if sub else 0)
        self.numbering = QCheckBox("Number files in playlist order")
        self.numbering.setChecked(bool(filters.get("numbering", True)))
        self.enabled = QCheckBox("Enabled")
        self.enabled.setChecked(sub.enabled if sub else True)

        self.min_dur = QSpinBox()
        self.min_dur.setRange(0, 1440)
        self.min_dur.setSuffix(" min")
        self.min_dur.setSpecialValueText("No minimum")
        self.min_dur.setValue(int(filters.get("min_duration", 0)) // 60)
        self.max_dur = QSpinBox()
        self.max_dur.setRange(0, 1440)
        self.max_dur.setSuffix(" min")
        self.max_dur.setSpecialValueText("No maximum")
        self.max_dur.setValue(int(filters.get("max_duration", 0)) // 60)
        self.include = QLineEdit(filters.get("include_keywords", ""))
        self.include.setPlaceholderText("comma separated, any match")
        self.exclude = QLineEdit(filters.get("exclude_keywords", ""))
        self.exclude.setPlaceholderText("e.g. live, trailer")
        self.skip_shorts = QCheckBox("Skip YouTube Shorts")
        self.skip_shorts.setChecked(bool(filters.get("skip_shorts", False)))
        self.use_date = QCheckBox("Only videos uploaded after")
        self.date_after = QDateEdit()
        self.date_after.setCalendarPopup(True)
        self.date_after.setDisplayFormat("yyyy-MM-dd")
        date = filters.get("date_after")
        self.use_date.setChecked(bool(date))
        self.date_after.setDate(QDate.fromString(date, "yyyyMMdd") if date else
                                QDate.currentDate().addMonths(-1))
        self.date_after.setEnabled(bool(date))
        self.use_date.toggled.connect(self.date_after.setEnabled)

        form = QFormLayout()
        form.addRow("Name", self.name)
        form.addRow("URL", self.url)
        form.addRow("Quality", self.preset)
        form.addRow("Folder", hbox(self.folder, browse))
        form.addRow("Check", self.interval)
        form.addRow("Keep latest", self.keep_last)
        form.addRow("", self.from_now_on)
        form.addRow("", self.numbering)
        form.addRow("", self.enabled)

        fbox = QGroupBox("Filters")
        fform = QFormLayout(fbox)
        fform.addRow("Min length", self.min_dur)
        fform.addRow("Max length", self.max_dur)
        fform.addRow("Title contains", self.include)
        fform.addRow("Title excludes", self.exclude)
        fform.addRow("", self.skip_shorts)
        fform.addRow(self.use_date, self.date_after)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(fbox)
        if not sub:
            note = QLabel("Keep latest deletes older videos from this subscription automatically.")
            note.setObjectName("Muted")
            lay.addWidget(note)
        lay.addWidget(buttons)

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Folder", self.folder.text())
        if folder:
            self.folder.setText(folder)

    def _validate(self) -> None:
        if not self.url.text().strip().startswith(("http://", "https://")):
            QMessageBox.warning(self, "Subscription", "Enter a valid playlist or channel URL.")
            return
        if not self.name.text().strip():
            self.name.setText(self.url.text().strip().rstrip("/").rsplit("/", 1)[-1])
        self.accept()

    def values(self) -> dict:
        filters = {
            "numbering": self.numbering.isChecked(),
            "min_duration": self.min_dur.value() * 60,
            "max_duration": self.max_dur.value() * 60,
            "include_keywords": self.include.text().strip(),
            "exclude_keywords": self.exclude.text().strip(),
            "skip_shorts": self.skip_shorts.isChecked(),
            "date_after": self.date_after.date().toString("yyyyMMdd")
            if self.use_date.isChecked() else "",
        }
        values = {
            "name": self.name.text().strip(),
            "url": self.url.text().strip(),
            "preset": self.preset.currentData(),
            "interval_minutes": self.interval.currentData(),
            "keep_last": self.keep_last.value(),
            "from_now_on": self.from_now_on.isChecked(),
            "enabled": self.enabled.isChecked(),
            "filters": filters,
        }
        if self.folder.text().strip():
            values["output_root"] = self.folder.text().strip()
        return values


class SubscriptionsPage(QWidget):
    def __init__(self, settings: SettingsStore, service: SubscriptionService, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.service = service

        self.tree = QTreeWidget()
        self.tree.setRootIsDecorated(False)
        self.tree.setHeaderLabels(["Name", "Quality", "Check", "Last checked", "Status", "URL"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setAlternatingRowColors(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.itemDoubleClicked.connect(lambda *_: self.edit())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.addLayout(hbox(
            page_header("Subscriptions",
                        "Playlists and channels checked automatically. New videos are queued."),
            None,
            primary_button("Add subscription", self.add, "plus"),
        ))
        layout.addLayout(hbox(
            tool_button("refresh", "Check selected now", self.check_selected, "Check now"),
            tool_button("sync", "Check all enabled", self.check_all, "Check all"),
            tool_button("pencil-outline", "Edit", self.edit, "Edit"),
            tool_button("folder-outline", "Open folder", self.open_folder),
            tool_button("delete-outline", "Delete", self.delete),
            None,
        ))
        layout.addWidget(self.tree, 1)

        service.changed.connect(self.reload)
        service.checked.connect(lambda *_: self.reload())
        self.reload()

    def reload(self) -> None:
        selected = set(self.selected_ids())
        self.tree.clear()
        for sub in self.service.all():
            if self.service.is_checking(sub.id):
                status = "Checking…"
            elif sub.last_error:
                status = f"Error: {sub.last_error.splitlines()[0]}"
            elif not sub.enabled:
                status = "Disabled"
            else:
                status = "OK" if sub.last_checked else "Not checked yet"
            item = QTreeWidgetItem([
                sub.name, presets.get(sub.preset).label, interval_label(sub.interval_minutes),
                f"{sub.last_checked:%Y-%m-%d %H:%M}" if sub.last_checked else "—", status, sub.url,
            ])
            item.setData(0, IdRole, sub.id)
            if sub.last_error:
                item.setForeground(4, theme.color("danger"))
                item.setToolTip(4, sub.last_error)
            if not sub.enabled:
                for col in range(6):
                    item.setForeground(col, theme.color("muted"))
            self.tree.addTopLevelItem(item)
            item.setSelected(sub.id in selected)
        for col in range(1, 5):
            self.tree.resizeColumnToContents(col)

    def selected_ids(self) -> list[int]:
        return [i.data(0, IdRole) for i in self.tree.selectedItems()]

    def add(self) -> None:
        dlg = SubscriptionDialog(self.settings, parent=self)
        if dlg.exec():
            values = dlg.values()
            sub = self.service.create(values.pop("name"), values.pop("url"), **values)
            self.service.check(sub.id)

    def edit(self) -> None:
        ids = self.selected_ids()
        sub = self.service.get(ids[0]) if ids else None
        if not sub:
            return
        dlg = SubscriptionDialog(self.settings, sub, self)
        if dlg.exec():
            self.service.update(sub.id, **dlg.values())

    def delete(self) -> None:
        ids = self.selected_ids()
        if ids and QMessageBox.question(
            self, "Delete subscriptions",
            f"Delete {len(ids)} subscription(s)? Downloaded files are kept."
        ) == QMessageBox.StandardButton.Yes:
            for sub_id in ids:
                self.service.delete(sub_id)

    def check_selected(self) -> None:
        for sub_id in self.selected_ids():
            self.service.check(sub_id)

    def check_all(self) -> None:
        for sub in self.service.all():
            if sub.enabled:
                self.service.check(sub.id)

    def open_folder(self) -> None:
        for sub_id in self.selected_ids()[:1]:
            sub = self.service.get(sub_id)
            if sub:
                fsutil.open_path(sub.output_root)

    def _toggle(self, enabled: bool) -> None:
        for sub_id in self.selected_ids():
            self.service.update(sub_id, enabled=enabled)

    def _context_menu(self, pos) -> None:
        if not self.selected_ids():
            return
        menu = QMenu(self)
        menu.addAction(action(menu, "Check now", self.check_selected, "refresh"))
        menu.addAction(action(menu, "Edit…", self.edit, "pencil-outline"))
        menu.addAction(action(menu, "Enable", lambda: self._toggle(True), "check"))
        menu.addAction(action(menu, "Disable", lambda: self._toggle(False), "cancel"))
        menu.addAction(action(menu, "Open folder", self.open_folder, "folder-outline"))
        menu.addSeparator()
        menu.addAction(action(menu, "Delete", self.delete, "delete-outline"))
        menu.exec(self.tree.viewport().mapToGlobal(pos))
