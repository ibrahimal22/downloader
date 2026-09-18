"""Small shared widgets and helpers."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from downloader.ui.icons import icon


def page_header(title: str, subtitle: str = "") -> QWidget:
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 4)
    lay.setSpacing(2)
    label = QLabel(title)
    label.setObjectName("PageTitle")
    lay.addWidget(label)
    if subtitle:
        sub = QLabel(subtitle)
        sub.setObjectName("Muted")
        lay.addWidget(sub)
    box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    return box


def tool_button(icon_name: str, tooltip: str, slot: Callable[[], None],
                text: str = "") -> QToolButton:
    btn = QToolButton()
    btn.setIcon(icon(icon_name))
    btn.setToolTip(tooltip)
    if text:
        btn.setText(text)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    btn.clicked.connect(slot)
    return btn


def primary_button(text: str, slot: Callable[[], None], icon_name: str | None = None) -> QPushButton:
    btn = QPushButton(text)
    btn.setProperty("primary", True)
    if icon_name:
        btn.setIcon(icon(icon_name, "accent_text"))
    btn.clicked.connect(slot)
    return btn


def action(parent, text: str, slot: Callable[[], None], icon_name: str | None = None,
           shortcut: str | None = None) -> QAction:
    act = QAction(text, parent)
    if icon_name:
        act.setIcon(icon(icon_name))
    if shortcut:
        act.setShortcut(shortcut)
    act.triggered.connect(slot)
    return act


def hbox(*widgets, stretch_at: int | None = None, spacing: int = 6) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    for n, w in enumerate(widgets):
        if n == stretch_at:
            lay.addStretch(1)
        if isinstance(w, QWidget):
            lay.addWidget(w)
        elif w is None:
            lay.addStretch(1)
        else:
            lay.addLayout(w)
    if stretch_at is not None and stretch_at >= len(widgets):
        lay.addStretch(1)
    return lay


def sync_rows(model: QAbstractItemModel, current: list, new: list) -> None:
    """Mutate `current` (a model's row keys) into `new` with proper model signals.

    Removals and insertions use begin/end*Rows; pure reordering uses a layout change with
    persistent-index remapping so selections survive.
    """
    new_set = set(new)
    for row in range(len(current) - 1, -1, -1):
        if current[row] not in new_set:
            model.beginRemoveRows(QModelIndex(), row, row)
            current.pop(row)
            model.endRemoveRows()
    have = set(current)
    missing = [key for key in new if key not in have]
    if missing:
        start = len(current)
        model.beginInsertRows(QModelIndex(), start, start + len(missing) - 1)
        current.extend(missing)
        model.endInsertRows()
    if current != new:
        model.layoutAboutToBeChanged.emit()
        old_indexes = model.persistentIndexList()
        old_keys = [current[i.row()] if 0 <= i.row() < len(current) else None
                    for i in old_indexes]
        current[:] = new
        pos = {key: row for row, key in enumerate(new)}
        new_indexes = [model.index(pos[k], i.column()) if k in pos else QModelIndex()
                       for k, i in zip(old_keys, old_indexes, strict=True)]
        model.changePersistentIndexList(old_indexes, new_indexes)
        model.layoutChanged.emit()
