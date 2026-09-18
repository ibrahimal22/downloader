"""7×24 grid for picking allowed download hours; click-drag paints on/off."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QMouseEvent, QPainter, QPainterPath
from PySide6.QtWidgets import QSizePolicy, QWidget

from downloader.ui import theme

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
LABEL_W = 44
HEADER_H = 20


class WeekGrid(QWidget):
    changed = Signal()

    def __init__(self, hours: list[list[bool]] | None = None, parent=None) -> None:
        super().__init__(parent)
        self.hours = [list(day) for day in (hours or [[True] * 24 for _ in range(7)])]
        self._paint_value: bool | None = None
        self.setMouseTracking(True)
        self.setMinimumSize(QSize(LABEL_W + 24 * 18, HEADER_H + 7 * 22))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setToolTip("Click or drag to toggle hours. Highlighted = downloads allowed.")

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(LABEL_W + 24 * 28, HEADER_H + 7 * 26)

    def set_hours(self, hours: list[list[bool]]) -> None:
        self.hours = [list(day) for day in hours]
        self.update()

    def _cell_size(self) -> tuple[float, float]:
        return ((self.width() - LABEL_W) / 24, (self.height() - HEADER_H) / 7)

    def _cell_at(self, pos: QPoint) -> tuple[int, int] | None:
        cw, ch = self._cell_size()
        x, y = pos.x() - LABEL_W, pos.y() - HEADER_H
        if x < 0 or y < 0:
            return None
        hour, day = int(x // cw), int(y // ch)
        if 0 <= hour < 24 and 0 <= day < 7:
            return day, hour
        return None

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cw, ch = self._cell_size()
        p.setPen(theme.color("muted"))
        for hour in range(0, 24, 3):
            p.drawText(QRect(int(LABEL_W + hour * cw), 0, int(cw * 3), HEADER_H),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f"{hour:02d}")
        for day in range(7):
            y = HEADER_H + day * ch
            p.setPen(theme.color("muted"))
            p.drawText(QRect(0, int(y), LABEL_W - 6, int(ch)),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, DAYS[day])
            for hour in range(24):
                cell = QRect(int(LABEL_W + hour * cw) + 1, int(y) + 1, int(cw) - 2, int(ch) - 2)
                path = QPainterPath()
                path.addRoundedRect(cell, 3, 3)
                on = self.hours[day][hour]
                p.fillPath(path, theme.color("accent" if on else "surface2"))
        p.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        cell = self._cell_at(event.position().toPoint())
        if cell and event.button() == Qt.MouseButton.LeftButton:
            day, hour = cell
            self._paint_value = not self.hours[day][hour]
            self._apply(cell)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        cell = self._cell_at(event.position().toPoint())
        if cell:
            self.setToolTip(f"{DAYS[cell[0]]} {cell[1]:02d}:00–{(cell[1] + 1) % 24:02d}:00")
        if self._paint_value is not None and cell:
            self._apply(cell)

    def mouseReleaseEvent(self, _event) -> None:  # noqa: N802
        self._paint_value = None

    def _apply(self, cell: tuple[int, int]) -> None:
        day, hour = cell
        if self.hours[day][hour] != self._paint_value:
            self.hours[day][hour] = bool(self._paint_value)
            self.update()
            self.changed.emit()
