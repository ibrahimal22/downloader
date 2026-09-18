"""Scheduler page: download window, bandwidth schedule, conditions, after-queue action."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHeaderView,
    QLabel,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from downloader.config.settings import BandwidthRule, ScheduleWindow, SettingsStore
from downloader.core.download_manager import DownloadManager
from downloader.scheduler import rules
from downloader.ui import fmt
from downloader.ui.widgets.common import hbox, page_header, tool_button
from downloader.ui.widgets.week_grid import WeekGrid

WINDOW_PRESETS = {
    "Always": lambda d, h: True,
    "Nights (01:00–07:00)": lambda d, h: 1 <= h < 7,
    "Nights + weekends": lambda d, h: d >= 5 or 1 <= h < 7,
    "Outside work hours": lambda d, h: d >= 5 or not (9 <= h < 18),
}

AFTER_QUEUE = [("Do nothing", "none"), ("Show a notification", "notify"),
               ("Put the computer to sleep", "sleep"), ("Shut down the computer", "shutdown")]


def speed_spin(value: int) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(0, 1_000_000)
    spin.setSingleStep(128)
    spin.setSuffix(" KB/s")
    spin.setSpecialValueText("Unlimited")
    spin.setValue(value)
    return spin


def hour_spin(value: int) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(0, 24)
    spin.setSuffix(":00")
    spin.setValue(value)
    return spin


class SchedulerPage(QWidget):
    def __init__(self, settings: SettingsStore, manager: DownloadManager, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.manager = manager
        s = settings.data
        self._save_timer = QTimer(self, singleShot=True, interval=400)
        self._save_timer.timeout.connect(self.save)

        # Window
        self.window_enabled = QCheckBox("Only download during these hours")
        self.window_enabled.setChecked(s.window.enabled)
        self.grid = WeekGrid(s.window.hours)
        self.grid.setEnabled(s.window.enabled)
        self.window_enabled.toggled.connect(self.grid.setEnabled)
        self.window_preset = QComboBox()
        self.window_preset.addItem("Apply a preset…")
        self.window_preset.addItems(list(WINDOW_PRESETS))
        self.window_preset.activated.connect(self._apply_window_preset)
        wbox = QGroupBox("Download window")
        wlay = QVBoxLayout(wbox)
        wlay.addLayout(hbox(self.window_enabled, None, self.window_preset))
        wlay.addWidget(self.grid)
        note = QLabel("Items added with “Only download inside the window” wait outside these "
                      "hours; running downloads pause and resume automatically. "
                      "“Start now” ignores the window.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        wlay.addWidget(note)

        # Bandwidth
        self.global_limit = speed_spin(s.global_limit_kbps)
        self.rules_table = QTableWidget(0, 3)
        self.rules_table.setHorizontalHeaderLabels(["From", "To", "Speed limit"])
        self.rules_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.rules_table.verticalHeader().hide()
        self.rules_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.rules_table.setMaximumHeight(180)
        for rule in s.bandwidth_rules:
            self._add_rule_row(rule)
        bbox = QGroupBox("Bandwidth")
        bform = QFormLayout(bbox)
        bform.addRow("Default limit", self.global_limit)
        bform.addRow(QLabel("Time-of-day rules (first match wins, overrides the default):"))
        bform.addRow(self.rules_table)
        bform.addRow(hbox(
            tool_button("plus", "Add rule", lambda: self._add_rule_row(
                BandwidthRule(start_hour=9, end_hour=18, limit_kbps=1024)), "Add rule"),
            tool_button("minus", "Remove selected rule", self._remove_rule, "Remove"),
            None))

        # Conditions & after-queue
        self.pause_battery = QCheckBox("Pause while running on battery")
        self.pause_battery.setChecked(s.pause_on_battery)
        self.pause_metered = QCheckBox("Pause on metered connections (Windows)")
        self.pause_metered.setChecked(s.pause_on_metered)
        self.after = QComboBox()
        for label, value in AFTER_QUEUE:
            self.after.addItem(label, value)
        self.after.setCurrentIndex(max(0, self.after.findData(s.after_queue_action)))
        self.max_concurrent = QSpinBox()
        self.max_concurrent.setRange(1, 16)
        self.max_concurrent.setValue(s.max_concurrent)
        self.max_per_site = QSpinBox()
        self.max_per_site.setRange(1, 16)
        self.max_per_site.setValue(s.max_per_site)
        cbox = QGroupBox("Queue")
        cform = QFormLayout(cbox)
        cform.addRow("Simultaneous downloads", self.max_concurrent)
        cform.addRow("Per website", self.max_per_site)
        cform.addRow("", self.pause_battery)
        cform.addRow("", self.pause_metered)
        cform.addRow("When the queue finishes", self.after)

        self.state_label = QLabel("")
        self.state_label.setObjectName("Muted")

        content = QWidget()
        lay = QVBoxLayout(content)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.addWidget(page_header("Scheduler",
                                  "Control when downloads run and how much bandwidth they use."))
        lay.addWidget(self.state_label)
        lay.addWidget(wbox)
        lay.addWidget(bbox)
        lay.addWidget(cbox)
        lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        for sig in (self.window_enabled.toggled, self.grid.changed, self.global_limit.valueChanged,
                    self.pause_battery.toggled, self.pause_metered.toggled,
                    self.after.currentIndexChanged, self.max_concurrent.valueChanged,
                    self.max_per_site.valueChanged):
            sig.connect(self._schedule_save)
        manager.stats.connect(lambda _s: self._update_state())
        self._update_state()

    # -------------------------------------------------------------- rules table

    def _add_rule_row(self, rule: BandwidthRule) -> None:
        row = self.rules_table.rowCount()
        self.rules_table.insertRow(row)
        widgets = (hour_spin(rule.start_hour), hour_spin(rule.end_hour),
                   speed_spin(rule.limit_kbps))
        for col, widget in enumerate(widgets):
            widget.valueChanged.connect(self._schedule_save)
            self.rules_table.setCellWidget(row, col, widget)
        self._schedule_save()

    def _remove_rule(self) -> None:
        rows = sorted({i.row() for i in self.rules_table.selectedIndexes()}, reverse=True)
        if not rows and self.rules_table.rowCount():
            rows = [self.rules_table.rowCount() - 1]
        for row in rows:
            self.rules_table.removeRow(row)
        self._schedule_save()

    def _rules(self) -> list[BandwidthRule]:
        out = []
        for row in range(self.rules_table.rowCount()):
            start, end, limit = (self.rules_table.cellWidget(row, c).value() for c in range(3))
            out.append(BandwidthRule(start_hour=start, end_hour=end, limit_kbps=limit))
        return out

    # -------------------------------------------------------------- saving

    def _apply_window_preset(self, index: int) -> None:
        if index == 0:
            return
        fn = WINDOW_PRESETS[self.window_preset.itemText(index)]
        self.grid.set_hours([[fn(d, h) for h in range(24)] for d in range(7)])
        self.window_enabled.setChecked(True)
        self.window_preset.setCurrentIndex(0)
        self._schedule_save()

    def _schedule_save(self, *_args) -> None:
        self._save_timer.start()

    def save(self) -> None:
        self.settings.update(
            window=ScheduleWindow(enabled=self.window_enabled.isChecked(), hours=self.grid.hours),
            bandwidth_rules=self._rules(),
            global_limit_kbps=self.global_limit.value(),
            pause_on_battery=self.pause_battery.isChecked(),
            pause_on_metered=self.pause_metered.isChecked(),
            after_queue_action=self.after.currentData(),
            max_concurrent=self.max_concurrent.value(),
            max_per_site=self.max_per_site.value(),
        )
        self.manager.tick()
        self._update_state()

    def _update_state(self) -> None:
        s = self.settings.data
        now = datetime.now()
        limit = rules.current_limit_kbps(s.bandwidth_rules, s.global_limit_kbps, now)
        blocked = self.manager.blocked_reason(None, now)
        state = blocked or "Downloads are allowed right now"
        self.state_label.setText(f"Now: {state} · Speed limit: {fmt.kbps(limit)}")
