"""Table model over DownloadManager items, plus a progress-bar delegate."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRectF, Qt
from PySide6.QtGui import QPainter, QPainterPath
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from downloader.core import presets
from downloader.core.download_manager import DownloadManager
from downloader.db.models import DownloadItem, Status
from downloader.ui import fmt, theme
from downloader.ui.icons import icon
from downloader.ui.widgets.common import sync_rows

ItemRole = Qt.ItemDataRole.UserRole + 1
ProgressRole = Qt.ItemDataRole.UserRole + 2

STATUS_ICONS = {
    Status.QUEUED: ("clock-outline", "muted"),
    Status.SCHEDULED: ("calendar-clock", "accent"),
    Status.WAITING: ("timer-sand", "warning"),
    Status.DOWNLOADING: ("download", "accent"),
    Status.PROCESSING: ("cog-outline", "accent"),
    Status.PAUSED: ("pause-circle-outline", "muted"),
    Status.COMPLETED: ("check-circle-outline", "success"),
    Status.FAILED: ("alert-circle-outline", "danger"),
    Status.CANCELED: ("close-circle-outline", "muted"),
}

COLUMNS = {
    "title": "Title",
    "status": "Status",
    "progress": "Progress",
    "size": "Size",
    "speed": "Speed",
    "eta": "ETA",
    "preset": "Quality",
    "uploader": "Channel",
    "added": "Added",
    "finished": "Finished",
}


class DownloadsModel(QAbstractTableModel):
    def __init__(self, manager: DownloadManager, columns: list[str],
                 source: Callable[[], list[DownloadItem]], parent=None) -> None:
        super().__init__(parent)
        self.manager = manager
        self.columns = columns
        self.source = source
        self.ids: list[int] = []
        self._icons: dict[Status, object] = {}
        manager.added.connect(lambda _id: self.refresh())
        manager.removed.connect(lambda _id: self.refresh())
        manager.changed.connect(self._on_changed)
        self.refresh()

    def refresh(self) -> None:
        sync_rows(self, self.ids, [i.id for i in self.source()])

    def _on_changed(self, item_id: int) -> None:
        wanted = {i.id for i in self.source()}
        if (item_id in wanted) != (item_id in self.ids):
            self.refresh()
            return
        if item_id in self.ids:
            row = self.ids.index(item_id)
            self.dataChanged.emit(self.index(row, 0), self.index(row, len(self.columns) - 1))

    def item_at(self, row: int) -> DownloadItem | None:
        return self.manager.get(self.ids[row]) if 0 <= row < len(self.ids) else None

    # -------------------------------------------------------------- Qt API

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.ids)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[self.columns[section]]
        return None

    def _status_icon(self, status: Status):
        if status not in self._icons:
            name, color = STATUS_ICONS[status]
            self._icons[status] = icon(name, color)
        return self._icons[status]

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        item = self.item_at(index.row())
        if item is None:
            return None
        col = self.columns[index.column()]
        if role == ItemRole:
            return item
        if role == ProgressRole:
            return item.progress
        task = self.manager.task(item.id)
        if role == Qt.ItemDataRole.DisplayRole:
            if col == "title":
                return item.title or item.url
            if col == "status":
                if item.status is Status.FAILED and item.error:
                    return f"Failed: {item.error.splitlines()[0]}"
                return item.stage or item.status.value.capitalize()
            if col == "progress":
                return f"{item.progress:.0f}%"
            if col == "size":
                return fmt.size(item.total_bytes)
            if col == "speed":
                return fmt.speed(task.state.speed) if task and not task.state.processing else ""
            if col == "eta":
                return fmt.eta(task.state.eta) if task and not task.state.processing else ""
            if col == "preset":
                return presets.get(item.preset).label
            if col == "uploader":
                return item.uploader or ""
            if col == "added":
                return f"{item.created_at:%Y-%m-%d %H:%M}" if item.created_at else ""
            if col == "finished":
                return f"{item.finished_at:%Y-%m-%d %H:%M}" if item.finished_at else ""
        if role == Qt.ItemDataRole.DecorationRole and col == "title":
            return self._status_icon(item.status)
        if role == Qt.ItemDataRole.ToolTipRole:
            lines = [item.title or "", item.url]
            if item.error:
                lines.append(f"\n{item.error}")
            if item.filepath:
                lines.append(f"\n{item.filepath}")
            return "\n".join(x for x in lines if x)
        if role == Qt.ItemDataRole.ForegroundRole and col == "status":
            if item.status is Status.FAILED:
                return theme.color("danger")
            if item.status in (Status.WAITING, Status.PAUSED, Status.QUEUED, Status.CANCELED):
                return theme.color("muted")
        return None


class ProgressDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget else None
        if style:
            style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, opt, painter,
                                opt.widget)
        value = float(index.data(ProgressRole) or 0.0)
        item = index.data(ItemRole)
        rect = QRectF(option.rect.adjusted(6, 0, -6, 0))
        bar_h = 6.0
        bar = QRectF(rect.left(), rect.center().y() + 2, rect.width() - 40, bar_h)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QPainterPath()
        track.addRoundedRect(bar, 3, 3)
        painter.fillPath(track, theme.color("border"))
        if value > 0:
            filled = QRectF(bar.left(), bar.top(), bar.width() * min(value, 100) / 100, bar_h)
            path = QPainterPath()
            path.addRoundedRect(filled, 3, 3)
            key = "accent"
            if item is not None:
                if item.status is Status.COMPLETED:
                    key = "success"
                elif item.status is Status.FAILED:
                    key = "danger"
                elif item.status in (Status.PAUSED, Status.WAITING):
                    key = "warning"
            painter.fillPath(path, theme.color(key))
        painter.setPen(theme.color("muted"))
        text_rect = QRectF(bar.right() + 4, option.rect.top(), 40, option.rect.height())
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         f"{value:.0f}%")
        painter.restore()
