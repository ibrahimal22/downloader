"""Main window: sidebar navigation, pages, status bar, tray icon, notifications."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from downloader.config.settings import SettingsStore
from downloader.core.download_manager import DownloadManager, NewDownload
from downloader.integrations import system
from downloader.library.service import LibraryService
from downloader.scheduler.service import MaintenanceScheduler
from downloader.subscriptions.sync import SubscriptionService
from downloader.ui import fmt
from downloader.ui.icons import app_icon, icon
from downloader.ui.pages.add_dialog import AddDialog, extract_urls
from downloader.ui.pages.downloads import DownloadsPage, HistoryPage
from downloader.ui.pages.library import LibraryPage
from downloader.ui.pages.scheduler import SchedulerPage
from downloader.ui.pages.settings import SettingsPage
from downloader.ui.pages.subscriptions import SubscriptionsPage

PAGES = [
    ("downloads", "Downloads", "download-circle-outline"),
    ("library", "Library", "filmstrip-box-multiple"),
    ("subscriptions", "Subscriptions", "playlist-play"),
    ("scheduler", "Scheduler", "calendar-clock"),
    ("history", "History", "history"),
    ("settings", "Settings", "cog-outline"),
]


class CountdownBox(QMessageBox):
    """'Shutting down in 60s' with a cancel button."""

    def __init__(self, action_label: str, seconds: int, parent=None) -> None:
        super().__init__(parent)
        self.action_label = action_label
        self.remaining = seconds
        self.setWindowTitle("Queue finished")
        self.setIcon(QMessageBox.Icon.Information)
        self.cancel_btn = self.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        self.now_btn = self.addButton(f"{action_label} now", QMessageBox.ButtonRole.AcceptRole)
        self._timer = QTimer(self, interval=1000)
        self._timer.timeout.connect(self._tick)
        self._update()
        self._timer.start()

    def _update(self) -> None:
        self.setText(f"All downloads are finished.\n{self.action_label} in {self.remaining} s…")

    def _tick(self) -> None:
        self.remaining -= 1
        if self.remaining <= 0:
            self._timer.stop()
            self.done(1)
        else:
            self._update()

    def run(self) -> bool:
        self.exec()
        self._timer.stop()
        return self.clickedButton() is not self.cancel_btn


class MainWindow(QMainWindow):
    def __init__(self, settings: SettingsStore, manager: DownloadManager,
                 library: LibraryService, subscriptions: SubscriptionService,
                 maintenance: MaintenanceScheduler) -> None:
        super().__init__()
        self.settings = settings
        self.manager = manager
        self.library = library
        self.subscriptions = subscriptions
        self.maintenance = maintenance
        self._quitting = False
        self._tray_hint_shown = False

        self.setWindowTitle("Downloader")
        self.setWindowIcon(app_icon())
        self.setAcceptDrops(True)
        self.resize(1280, 800)
        self.setMinimumSize(QSize(900, 560))

        # Pages
        self.downloads_page = DownloadsPage(manager)
        self.library_page = LibraryPage(library, manager)
        self.subscriptions_page = SubscriptionsPage(settings, subscriptions)
        self.scheduler_page = SchedulerPage(settings, manager)
        self.history_page = HistoryPage(manager)
        self.settings_page = SettingsPage(settings, maintenance)
        self.downloads_page.add_requested.connect(lambda: self.open_add())
        pages = {
            "downloads": self.downloads_page, "library": self.library_page,
            "subscriptions": self.subscriptions_page, "scheduler": self.scheduler_page,
            "history": self.history_page, "settings": self.settings_page,
        }

        self.stack = QStackedWidget()
        self.nav = QListWidget()
        self.nav.setObjectName("Sidebar")
        self.nav.setFixedWidth(210)
        self.nav.setIconSize(QSize(20, 20))
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._nav_items: dict[str, QListWidgetItem] = {}
        for key, label, icon_name in PAGES:
            item = QListWidgetItem(icon(icon_name), label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.nav.addItem(item)
            self._nav_items[key] = item
            self.stack.addWidget(pages[key])
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)

        brand = QLabel("  Downloader")
        brand.setObjectName("Brand")
        brand.setFixedWidth(210)
        side = QWidget()
        side.setObjectName("SidebarPanel")
        side_lay = QVBoxLayout(side)
        side_lay.setContentsMargins(0, 0, 0, 0)
        side_lay.setSpacing(0)
        side_lay.addWidget(brand)
        side_lay.addWidget(self.nav, 1)

        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(side)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        # Status bar
        self.status_active = QLabel()
        self.status_speed = QLabel()
        self.status_state = QLabel()
        self.status_library = QLabel()
        bar = self.statusBar()
        bar.setSizeGripEnabled(False)
        for widget in (self.status_active, self.status_speed, self.status_state):
            bar.addWidget(widget)
        bar.addPermanentWidget(self.status_library)

        # Shortcuts
        paste = QAction(self)
        paste.setShortcut(QKeySequence.StandardKey.Paste)
        paste.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        paste.triggered.connect(self._paste)
        self.addAction(paste)
        new = QAction(self)
        new.setShortcut(QKeySequence.StandardKey.New)
        new.triggered.connect(lambda: self.open_add())
        self.addAction(new)

        self._setup_tray()
        manager.stats.connect(self._on_stats)
        manager.failed.connect(self._on_failed)
        manager.completed.connect(self._on_completed)
        manager.queue_finished.connect(self._on_queue_finished)
        subscriptions.checked.connect(self._on_subscription_checked)
        library.changed.connect(self._update_library_status)
        self._update_library_status()
        self._restore_geometry()

    # -------------------------------------------------------------- tray

    def _setup_tray(self) -> None:
        self.tray = QSystemTrayIcon(app_icon(), self)
        menu = QMenu()
        menu.addAction(icon("window-maximize"), "Open Downloader", self.show_normal)
        menu.addAction(icon("plus"), "Add from clipboard", self._paste)
        menu.addSeparator()
        menu.addAction(icon("pause-circle-outline"), "Pause all", self.manager.pause_all)
        menu.addAction(icon("play-circle-outline"), "Resume all", self.manager.resume_all)
        menu.addSeparator()
        menu.addAction(icon("power"), "Quit", self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.setToolTip("Downloader")
        self.tray.show()
        self._tray_menu = menu

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            if self.isVisible() and not self.isMinimized():
                self.hide()
            else:
                self.show_normal()

    def notify(self, title: str, message: str,
               kind: QSystemTrayIcon.MessageIcon = QSystemTrayIcon.MessageIcon.Information) -> None:
        if QSystemTrayIcon.supportsMessages():
            self.tray.showMessage(title, message, kind, 6000)

    def show_normal(self) -> None:
        self.show()
        self.setWindowState((self.windowState() & ~Qt.WindowState.WindowMinimized) |
                            Qt.WindowState.WindowActive)
        self.raise_()
        self.activateWindow()

    # -------------------------------------------------------------- adding

    def open_add(self, text: str = "") -> None:
        self.show_normal()
        dlg = AddDialog(self.settings, self.manager, self.subscriptions, text, self)
        if dlg.exec():
            self.nav.setCurrentRow(0)

    def quick_add(self, url: str, preset: str = "") -> None:
        """Queue a single URL without the dialog (used by the browser extension)."""
        s = self.settings.data
        added, skipped = self.manager.add([NewDownload(
            url=url, output_root=s.download_dir, preset=preset or s.default_preset)])
        if added:
            self.notify("Download queued", url)
        elif skipped:
            self.notify("Already downloaded", url)

    def _paste(self) -> None:
        focus = QApplication.focusWidget()
        if isinstance(focus, (QLineEdit, QPlainTextEdit)):
            focus.paste()
            return
        text = QGuiApplication.clipboard().text()
        self.open_add(text if extract_urls(text) else "")

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        mime = event.mimeData()
        if mime.hasUrls() or mime.hasText():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        mime = event.mimeData()
        text = "\n".join(u.toString() for u in mime.urls()) if mime.hasUrls() else mime.text()
        if extract_urls(text):
            self.open_add(text)

    # -------------------------------------------------------------- status & events

    def _on_stats(self, stats: dict) -> None:
        active, queued = stats["active"], stats["queued"]
        self.status_active.setText(f"{active} downloading · {queued} waiting")
        speed = fmt.speed(stats["speed"]) or "0 B/s"
        limit = stats["limit_kbps"]
        self.status_speed.setText(f"↓ {speed}" + (f" (limit {fmt.kbps(limit)})" if limit else ""))
        state = "Paused" if stats.get("paused_all") else (stats.get("blocked_reason") or "")
        self.status_state.setText(state)
        badge = f"Downloads ({active + queued})" if active + queued else "Downloads"
        self._nav_items["downloads"].setText(badge)
        self.tray.setToolTip(f"Downloader — {active} active, {speed}")

    def _update_library_status(self) -> None:
        stats = self.library.stats()
        self.status_library.setText(f"Library: {stats['count']} items · {fmt.size(stats['size'])}")

    def _on_completed(self, item_id: int, _info: dict) -> None:
        item = self.manager.get(item_id)
        if item and not self.isActiveWindow():
            self.notify("Download complete", item.title or item.url)

    def _on_failed(self, item_id: int, error: str) -> None:
        item = self.manager.get(item_id)
        title = item.title or item.url if item else ""
        self.notify("Download failed", f"{title}\n{error.splitlines()[0]}",
                    QSystemTrayIcon.MessageIcon.Warning)

    def _on_subscription_checked(self, sub_id: int, queued: int, error: str) -> None:
        sub = self.subscriptions.get(sub_id)
        if sub and queued:
            self.notify("New videos", f"{sub.name}: {queued} new video(s) queued")

    def _on_queue_finished(self) -> None:
        action = self.settings.data.after_queue_action
        if action == "notify":
            self.notify("All downloads finished", "Your queue is empty.")
        elif action in ("sleep", "shutdown"):
            label = "Sleep" if action == "sleep" else "Shut down"
            if CountdownBox(label, 60, self).run():
                if action == "sleep":
                    system.sleep()  # returns after the machine wakes up
                else:
                    self.manager.shutdown()
                    system.shutdown()

    # -------------------------------------------------------------- window state

    def _restore_geometry(self) -> None:
        raw = self.settings.data.window_geometry
        if raw:
            self.restoreGeometry(QByteArray.fromBase64(raw.encode()))

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self.settings.update(
            window_geometry=bytes(self.saveGeometry().toBase64().data()).decode())
        if self.settings.data.minimize_to_tray and not self._quitting and \
                QSystemTrayIcon.isSystemTrayAvailable():
            event.ignore()
            self.hide()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                self.notify("Still running", "Downloader keeps working in the tray. "
                            "Right-click the tray icon to quit.")
            return
        self.quit()
        event.accept()

    def quit(self) -> None:
        active = len(self.manager.tasks)
        if active and QMessageBox.question(
            self, "Quit", f"{active} download(s) are running. They will resume next time. Quit?"
        ) != QMessageBox.StandardButton.Yes:
            return
        self._quitting = True
        self.tray.hide()
        QApplication.quit()
