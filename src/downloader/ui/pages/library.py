"""Library: searchable grid/list of downloaded media."""

from __future__ import annotations

from html import escape
from pathlib import Path

from PySide6.QtCore import QAbstractListModel, QModelIndex, QRect, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QFontMetrics, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from downloader.core import fsutil, presets
from downloader.core.download_manager import DownloadManager, NewDownload
from downloader.core.workers import run_async
from downloader.db.models import LibraryItem
from downloader.library.service import LibraryService, Query
from downloader.ui import fmt, theme
from downloader.ui.icons import icon
from downloader.ui.widgets.common import action, hbox, page_header, tool_button

ItemRole = Qt.ItemDataRole.UserRole + 1
THUMB = QSize(224, 126)


class ThumbCache:
    def __init__(self) -> None:
        self._cache: dict[str, QPixmap] = {}

    def get(self, path: str | None) -> QPixmap | None:
        if not path:
            return None
        if path not in self._cache:
            pix = QPixmap(path)
            self._cache[path] = pix.scaled(
                THUMB, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation) if not pix.isNull() else QPixmap()
        pix = self._cache[path]
        return None if pix.isNull() else pix


class LibraryModel(QAbstractListModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.rows: list[LibraryItem] = []

    def set_rows(self, rows: list[LibraryItem]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        item = self.rows[index.row()]
        if role == ItemRole:
            return item
        if role == Qt.ItemDataRole.DisplayRole:
            return item.title
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"{item.title}\n{item.uploader or ''}\n{item.filepath}"
        return None


class GridDelegate(QStyledItemDelegate):
    """Card with thumbnail, duration badge, title and channel."""

    def __init__(self, thumbs: ThumbCache, parent=None) -> None:
        super().__init__(parent)
        self.thumbs = thumbs
        self.list_mode = False

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        if self.list_mode:
            return QSize(600, 72)
        return QSize(THUMB.width() + 16, THUMB.height() + 70)

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        item: LibraryItem = index.data(ItemRole)
        rect = option.rect.adjusted(6, 6, -6, -6)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if selected or hovered:
            bg = QPainterPath()
            bg.addRoundedRect(rect.adjusted(-3, -3, 3, 3), 8, 8)
            painter.fillPath(bg, theme.color("selection" if selected else "surface2"))

        if self.list_mode:
            thumb_rect = QRect(rect.left(), rect.top(), int(rect.height() * 16 / 9), rect.height())
            text_left = thumb_rect.right() + 12
        else:
            thumb_rect = QRect(rect.left(), rect.top(), rect.width(), THUMB.height())
            text_left = rect.left()

        clip = QPainterPath()
        clip.addRoundedRect(thumb_rect, 6, 6)
        painter.setClipPath(clip)
        pix = self.thumbs.get(item.thumbnail_path)
        if pix:
            scaled = pix.scaled(thumb_rect.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                Qt.TransformationMode.SmoothTransformation)
            sx = (scaled.width() - thumb_rect.width()) // 2
            sy = (scaled.height() - thumb_rect.height()) // 2
            painter.drawPixmap(thumb_rect, scaled, QRect(sx, sy, thumb_rect.width(),
                                                         thumb_rect.height()))
        else:
            painter.fillRect(thumb_rect, theme.color("surface2"))
            glyph = icon("music-note" if item.is_audio else "movie-open-outline", "muted")
            s = min(48, thumb_rect.height() // 2)
            glyph.paint(painter, QRect(thumb_rect.center().x() - s // 2,
                                       thumb_rect.center().y() - s // 2, s, s))
        painter.setClipping(False)

        if item.duration:
            badge = fmt.duration(item.duration)
            font = QFont(option.font)
            font.setPointSizeF(font.pointSizeF() * 0.85)
            painter.setFont(font)
            fm = QFontMetrics(font)
            w, h = fm.horizontalAdvance(badge) + 10, fm.height() + 2
            badge_rect = QRect(thumb_rect.right() - w - 4, thumb_rect.bottom() - h - 4, w, h)
            path = QPainterPath()
            path.addRoundedRect(badge_rect, 4, 4)
            painter.fillPath(path, Qt.GlobalColor.black)
            painter.setPen(Qt.GlobalColor.white)
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, badge)

        title_font = QFont(option.font)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(theme.color("danger") if item.missing else theme.color("text"))
        if self.list_mode:
            text_rect = QRect(text_left, rect.top() + 4, rect.right() - text_left, 22)
        else:
            text_rect = QRect(text_left, thumb_rect.bottom() + 6, rect.width(), 36)
        fm = QFontMetrics(title_font)
        flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap
        title = item.title + (" (missing)" if item.missing else "")
        if self.list_mode:
            title = fm.elidedText(title, Qt.TextElideMode.ElideRight, text_rect.width())
            flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        else:
            title = fm.elidedText(title, Qt.TextElideMode.ElideRight, text_rect.width() * 2 - 20)
        painter.drawText(text_rect, flags, title)

        painter.setFont(option.font)
        painter.setPen(theme.color("muted"))
        meta = " · ".join(x for x in (item.uploader, fmt.size(item.size_bytes),
                                      f"{item.height}p" if item.height else "",
                                      fmt.upload_date(item.upload_date)) if x and x != "—")
        meta_rect = QRect(text_left, text_rect.bottom() + 2, text_rect.width(), 20)
        painter.drawText(meta_rect, Qt.AlignmentFlag.AlignLeft,
                         QFontMetrics(option.font).elidedText(meta, Qt.TextElideMode.ElideRight,
                                                              meta_rect.width()))
        painter.restore()


class DuplicatesDialog(QDialog):
    def __init__(self, library: LibraryService, groups: list[list[LibraryItem]], parent=None):
        super().__init__(parent)
        self.library = library
        self.setWindowTitle("Duplicates")
        self.resize(760, 480)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["File", "Size", "Added"])
        self.tree.setColumnWidth(0, 480)
        for n, group in enumerate(groups, start=1):
            top = QTreeWidgetItem([f"Group {n}: {group[0].title}", "", ""])
            for rec in group:
                child = QTreeWidgetItem([rec.filepath, fmt.size(rec.size_bytes),
                                         f"{rec.added_at:%Y-%m-%d}" if rec.added_at else ""])
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                child.setCheckState(0, Qt.CheckState.Unchecked)
                child.setData(0, ItemRole, rec.id)
                top.addChild(child)
            self.tree.addTopLevelItem(top)
            top.setExpanded(True)
        delete = tool_button("delete-outline", "Delete checked files", self._delete,
                             "Delete checked files")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Check the copies you want to delete (the file is removed from disk)."))
        lay.addWidget(self.tree, 1)
        lay.addLayout(hbox(None, delete))

    def _delete(self) -> None:
        ids = []
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for j in range(top.childCount()):
                child = top.child(j)
                if child.checkState(0) == Qt.CheckState.Checked:
                    ids.append(child.data(0, ItemRole))
        if ids and QMessageBox.question(self, "Delete", f"Delete {len(ids)} file(s)?") == \
                QMessageBox.StandardButton.Yes:
            self.library.delete(ids, delete_files=True)
            self.accept()


class LibraryPage(QWidget):
    def __init__(self, library: LibraryService, manager: DownloadManager, parent=None) -> None:
        super().__init__(parent)
        self.library = library
        self.manager = manager
        self.thumbs = ThumbCache()
        self._filter_uploader: str | None = None
        self._filter_playlist: str | None = None

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search titles, channels, descriptions, tags…")
        self.search.setClearButtonEnabled(True)
        self._debounce = QTimer(self, singleShot=True, interval=250)
        self._debounce.timeout.connect(self.reload)
        self.search.textChanged.connect(lambda _t: self._debounce.start())

        self.kind = QComboBox()
        self.kind.addItem("All media", "all")
        self.kind.addItem("Video", "video")
        self.kind.addItem("Audio", "audio")
        self.sort = QComboBox()
        for label, key in (("Recently added", "added_desc"), ("Oldest added", "added_asc"),
                           ("Title", "title"), ("Channel", "uploader"),
                           ("Upload date", "date"), ("Longest", "duration"), ("Largest", "size")):
            self.sort.addItem(label, key)
        self.view_mode = QComboBox()
        self.view_mode.addItem(icon("view-grid-outline"), "Grid", "grid")
        self.view_mode.addItem(icon("view-list-outline"), "List", "list")
        for combo in (self.kind, self.sort):
            combo.currentIndexChanged.connect(lambda _i: self.reload())
        self.view_mode.currentIndexChanged.connect(self._apply_view_mode)

        self.facets = QListWidget()
        self.facets.setObjectName("Facets")
        self.facets.setMaximumWidth(260)
        self.facets.itemClicked.connect(self._facet_clicked)

        self.model = LibraryModel(self)
        self.delegate = GridDelegate(self.thumbs, self)
        self.view = QListView()
        self.view.setModel(self.model)
        self.view.setItemDelegate(self.delegate)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.view.setMouseTracking(True)
        self.view.setUniformItemSizes(True)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._context_menu)
        self.view.doubleClicked.connect(lambda _i: self.play())
        self.view.selectionModel().selectionChanged.connect(lambda *_: self._show_details())

        self.details = QTextBrowser()
        self.details.setOpenExternalLinks(True)
        self.details.setMaximumWidth(340)

        self.summary = QLabel("")
        self.summary.setObjectName("Muted")

        split = QSplitter()
        split.addWidget(self.facets)
        split.addWidget(self.view)
        split.addWidget(self.details)
        split.setStretchFactor(1, 1)
        split.setSizes([180, 820, 280])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.addLayout(hbox(
            page_header("Library", "Everything you've downloaded, organized and searchable."),
            None,
            tool_button("folder-plus-outline", "Import an existing folder", self.import_folder,
                        "Import folder"),
            tool_button("file-search-outline", "Find files that were moved or deleted",
                        self.check_missing, "Check missing"),
            tool_button("content-duplicate", "Find duplicate files", self.find_duplicates,
                        "Duplicates"),
        ))
        layout.addLayout(hbox(self.search, self.kind, self.sort, self.view_mode))
        layout.addWidget(split, 1)
        layout.addWidget(self.summary)

        library.changed.connect(self._schedule_reload)
        self._apply_view_mode()
        self.reload()

    # -------------------------------------------------------------- data

    def _schedule_reload(self) -> None:
        self._debounce.start()

    def reload(self) -> None:
        q = Query(text=self.search.text(), kind=self.kind.currentData(),
                  sort=self.sort.currentData(), uploader=self._filter_uploader,
                  playlist=self._filter_playlist, limit=2000)
        self.model.set_rows(self.library.search(q))
        self._reload_facets()
        stats = self.library.stats()
        shown = self.model.rowCount()
        self.summary.setText(f"Showing {shown} of {stats['count']} items · "
                             f"{fmt.size(stats['size'])} on disk")
        self._show_details()

    def _reload_facets(self) -> None:
        facets = self.library.facets()
        self.facets.blockSignals(True)
        self.facets.clear()

        def add(label: str, kind: str | None, value: str | None, header: bool = False) -> None:
            item = QListWidgetItem(label)
            item.setData(ItemRole, (kind, value))
            if header:
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            elif (kind == "uploader" and value == self._filter_uploader) or \
                    (kind == "playlist" and value == self._filter_playlist) or \
                    (kind is None and not self._filter_uploader and not self._filter_playlist):
                item.setSelected(True)
            self.facets.addItem(item)
            if (kind, value) != (None, None) and item.isSelected():
                self.facets.setCurrentItem(item)

        add("All items", None, None)
        if facets["playlists"]:
            add("Playlists", None, "__h1", header=True)
            for name, count in facets["playlists"]:
                add(f"  {name} ({count})", "playlist", name)
        if facets["uploaders"]:
            add("Channels", None, "__h2", header=True)
            for name, count in facets["uploaders"][:200]:
                add(f"  {name} ({count})", "uploader", name)
        self.facets.blockSignals(False)

    def _facet_clicked(self, item: QListWidgetItem) -> None:
        kind, value = item.data(ItemRole)
        self._filter_uploader = value if kind == "uploader" else None
        self._filter_playlist = value if kind == "playlist" else None
        self.reload()

    def _apply_view_mode(self) -> None:
        grid = self.view_mode.currentData() == "grid"
        self.delegate.list_mode = not grid
        self.view.setViewMode(QListView.ViewMode.IconMode if grid else QListView.ViewMode.ListMode)
        self.view.setResizeMode(QListView.ResizeMode.Adjust)
        self.view.setMovement(QListView.Movement.Static)
        self.view.setSpacing(4)
        self.view.setWrapping(grid)
        self.view.doItemsLayout()

    def selected(self) -> list[LibraryItem]:
        return [i.data(ItemRole) for i in self.view.selectionModel().selectedIndexes()]

    def _show_details(self) -> None:
        items = self.selected()
        if not items:
            self.details.setHtml(f"<p style='color:{theme.color('muted').name()}'>"
                                 "Select an item to see details.</p>")
            return
        it = items[0]
        rows = [
            ("Channel", it.uploader), ("Playlist", it.playlist_title),
            ("Duration", fmt.duration(it.duration)), ("Size", fmt.size(it.size_bytes)),
            ("Resolution", f"{it.width}×{it.height}" if it.height else ""),
            ("Uploaded", fmt.upload_date(it.upload_date)), ("Site", it.extractor),
            ("File", it.filepath),
        ]
        table ="".join(f"<tr><td style='color:{theme.color('muted').name()};padding-right:8px'>"
                        f"{k}</td><td>{escape(str(v))}</td></tr>" for k, v in rows if v)
        link = f"<p><a href='{escape(it.source_url)}'>Open source page</a></p>" \
            if it.source_url else ""
        desc = escape(it.description or "").replace("\n", "<br>")
        self.details.setHtml(f"<h3>{escape(it.title)}</h3><table>{table}</table>{link}"
                             f"<p>{desc}</p>")

    # -------------------------------------------------------------- actions

    def play(self) -> None:
        for it in self.selected()[:1]:
            if it.missing:
                QMessageBox.warning(self, "File missing", f"{it.filepath}\nwas moved or deleted.")
            else:
                fsutil.open_path(it.filepath)

    def reveal(self) -> None:
        for it in self.selected()[:1]:
            fsutil.reveal_in_folder(it.filepath)

    def open_source(self) -> None:
        for it in self.selected()[:1]:
            if it.source_url:
                QDesktopServices.openUrl(QUrl(it.source_url))

    def redownload(self, preset_id: str) -> None:
        items = [it for it in self.selected() if it.source_url]
        new = [NewDownload(url=it.source_url, output_root=str(Path(it.filepath).parent),
                           preset=preset_id, title=it.title, uploader=it.uploader,
                           respect_window=False) for it in items]
        self.manager.add(new, skip_duplicates=False)  # explicit re-download

    def delete(self, with_files: bool) -> None:
        items = self.selected()
        if not items:
            return
        what = "Delete files from disk" if with_files else "Remove from library (files stay)"
        if QMessageBox.question(self, "Delete", f"{what} for {len(items)} item(s)?") == \
                QMessageBox.StandardButton.Yes:
            self.library.delete([i.id for i in items], delete_files=with_files)

    def _context_menu(self, pos) -> None:
        if not self.selected():
            return
        menu = QMenu(self)
        menu.addAction(action(menu, "Play", self.play, "play-circle-outline"))
        menu.addAction(action(menu, "Show in folder", self.reveal, "folder-outline"))
        menu.addAction(action(menu, "Open source page", self.open_source, "web"))
        sub = menu.addMenu(icon("refresh"), "Download again as…")
        for preset in presets.PRESETS.values():
            sub.addAction(action(sub, preset.label, lambda p=preset.id: self.redownload(p)))
        menu.addSeparator()
        menu.addAction(action(menu, "Remove from library", lambda: self.delete(False),
                              "playlist-remove"))
        menu.addAction(action(menu, "Delete file", lambda: self.delete(True), "delete-outline"))
        menu.exec(self.view.viewport().mapToGlobal(pos))

    def import_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Import folder into library")
        if not folder:
            return
        dlg = QProgressDialog("Scanning…", "", 0, 0, self)
        dlg.setWindowTitle("Import folder")
        dlg.setCancelButton(None)
        dlg.setMinimumDuration(0)
        dlg.show()

        def progress(value) -> None:
            done, total = value
            dlg.setMaximum(total)
            dlg.setValue(done)
            dlg.setLabelText(f"Indexed {done} of {total} files…")

        def finished(count: int) -> None:
            dlg.close()
            QMessageBox.information(self, "Import folder", f"Added {count} file(s) to the library.")

        def failed(exc: Exception) -> None:
            dlg.close()
            QMessageBox.warning(self, "Import folder", f"Import failed: {exc}")

        run_async(lambda cb: self.library.import_folder(folder, lambda d, t: cb((d, t))),
                  on_done=finished, on_error=failed, on_progress=progress)

    def check_missing(self) -> None:
        run_async(self.library.verify_files, on_done=lambda n: QMessageBox.information(
            self, "Check missing", f"{n} file(s) are missing from disk." if n else
            "All library files are present."))

    def find_duplicates(self) -> None:
        groups = self.library.duplicate_groups()
        if not groups:
            QMessageBox.information(self, "Duplicates", "No duplicates found.")
            return
        DuplicatesDialog(self.library, groups, self).exec()
