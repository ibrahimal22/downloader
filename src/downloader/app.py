"""Application bootstrap: wires services, UI and integrations together."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from downloader import __version__
from downloader.config.settings import SettingsStore
from downloader.core import binaries
from downloader.core.download_manager import DownloadManager
from downloader.core.workers import run_async
from downloader.db.session import init_db
from downloader.integrations import system
from downloader.integrations.clipboard import ClipboardWatcher
from downloader.integrations.local_server import LocalServer
from downloader.integrations.single_instance import InstanceServer, forward_to_running
from downloader.library.service import LibraryService
from downloader.logging_setup import setup_logging
from downloader.scheduler.service import MaintenanceScheduler
from downloader.subscriptions.sync import SubscriptionService
from downloader.ui import theme
from downloader.ui.first_run import FirstRunDialog
from downloader.ui.icons import app_icon
from downloader.ui.main_window import MainWindow
from downloader.updater import app_updater

log = logging.getLogger(__name__)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="downloader")
    parser.add_argument("urls", nargs="*", help="links to add (or downloader:// URLs)")
    parser.add_argument("--minimized", action="store_true", help="start in the tray")
    parser.add_argument("--version", action="version", version=__version__)
    return parser.parse_args(argv)


def normalize_urls(raw: list[str]) -> list[str]:
    urls = []
    for arg in raw:
        url = system.url_from_protocol(arg) or (arg if arg.startswith(("http://", "https://"))
                                                else None)
        if url:
            urls.append(url)
    return urls


class Application:
    def __init__(self, qt_app: QApplication, args: argparse.Namespace) -> None:
        self.qt = qt_app
        self.args = args
        self.settings = SettingsStore()
        theme.apply(qt_app, self.settings.data.theme)
        self._theme = self.settings.data.theme
        self.settings.subscribe(self._on_settings_changed)

        init_db()
        self.library = LibraryService(self.settings)
        self.manager = DownloadManager(self.settings, is_duplicate=self.library.is_duplicate)
        # Library must record a finished download before subscription retention runs.
        self.manager.completed.connect(self._record_in_library)
        self.subscriptions = SubscriptionService(self.settings, self.manager, self.library)
        self.maintenance = MaintenanceScheduler(self.settings, self.subscriptions, self.library)

        self.instance = InstanceServer()
        self.instance.message.connect(self._on_second_instance)
        self.instance.listen()

    def _record_in_library(self, item_id: int, info: dict) -> None:
        item = self.manager.get(item_id)
        if item:
            try:
                self.library.add_from_download(item, info)
            except Exception:  # noqa: BLE001 - never let indexing break the queue
                log.exception("Failed to add #%s to library", item_id)

    def _on_settings_changed(self, settings) -> None:
        if settings.theme != self._theme:
            self._theme = settings.theme
            theme.apply(self.qt, settings.theme)

    def run(self) -> int:
        s = self.settings.data
        needs_setup = not s.first_run_done or not s.legal_accepted or \
            binaries.missing(s.ytdlp_channel)
        if needs_setup and not FirstRunDialog(self.settings).exec():
            return 0

        self.manager.load()
        self.window = MainWindow(self.settings, self.manager, self.library, self.subscriptions,
                                 self.maintenance)

        self.server = LocalServer(self.settings)
        self.server.add_requested.connect(self._on_extension_add)
        self.server.start()
        self.window.settings_page.server_settings_changed.connect(self.server.restart)

        self.clipboard = ClipboardWatcher(self.settings)
        self.clipboard.url_detected.connect(self._on_clipboard_url)

        system.register_protocol_safe()
        self.manager.start()
        self.maintenance.start()
        self.qt.aboutToQuit.connect(self._shutdown)

        if not self.args.minimized:
            self.window.show()
        urls = normalize_urls(self.args.urls)
        if urls:
            QTimer.singleShot(200, lambda: self.window.open_add("\n".join(urls)))
        if s.check_app_updates:
            QTimer.singleShot(8000, self._check_app_update)
        return self.qt.exec()

    # -------------------------------------------------------------- integrations

    def _on_second_instance(self, argv: list) -> None:
        try:
            args = parse_args([str(a) for a in argv])
        except SystemExit:
            args = argparse.Namespace(urls=[], minimized=False)
        urls = normalize_urls(args.urls)
        if urls:
            self.window.open_add("\n".join(urls))
        else:
            self.window.show_normal()

    def _on_extension_add(self, url: str, preset: str, quick: bool) -> None:
        if quick:
            self.window.quick_add(url, preset)
        else:
            self.window.open_add(url)

    def _on_clipboard_url(self, url: str) -> None:
        if self.window.isActiveWindow():
            return  # the user is working in the app; Ctrl+V already covers this
        self.window.notify("Video link copied", f"Click to download:\n{url}")
        self._pending_clipboard_url = url
        try:
            self.window.tray.messageClicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.window.tray.messageClicked.connect(
            lambda: self.window.open_add(self._pending_clipboard_url))

    def _check_app_update(self) -> None:
        def done(release) -> None:
            self.settings.update(last_app_update_check=datetime.now().isoformat(timespec="seconds"))
            if not release or release.version == self.settings.data.skipped_app_version:
                return
            box = QMessageBox(self.window)
            box.setWindowTitle("Update available")
            box.setText(f"Downloader {release.version} is available (you have {__version__}).")
            box.setDetailedText(release.notes[:4000])
            install = box.addButton("Install now", QMessageBox.ButtonRole.AcceptRole)
            skip = box.addButton("Skip this version", QMessageBox.ButtonRole.DestructiveRole)
            box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is install:
                self._install_update(release)
            elif box.clickedButton() is skip:
                self.settings.update(skipped_app_version=release.version)

        run_async(app_updater.check_latest, on_done=done,
                  on_error=lambda e: log.info("Update check failed: %s", e))

    def _install_update(self, release) -> None:
        def done(path) -> None:
            app_updater.launch_installer(path)
            self.window.quit()

        run_async(lambda: app_updater.download_installer(release), on_done=done,
                  on_error=lambda e: QMessageBox.warning(self.window, "Update failed", str(e)))

    def _shutdown(self) -> None:
        log.info("Shutting down")
        self.manager.shutdown()
        self.server.stop()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    setup_logging()
    args = parse_args(argv)

    qt_app = QApplication(sys.argv[:1])
    qt_app.setApplicationName("Downloader")
    qt_app.setOrganizationName("Downloader")
    qt_app.setApplicationVersion(__version__)
    qt_app.setWindowIcon(app_icon())
    qt_app.setQuitOnLastWindowClosed(False)

    if forward_to_running(argv):
        log.info("Forwarded arguments to the running instance")
        return 0

    try:
        app = Application(qt_app, args)
    except Exception as exc:  # noqa: BLE001 - last-resort error for the user
        log.exception("Startup failed")
        QMessageBox.critical(None, "Downloader", f"Downloader could not start:\n{exc}")
        return 1
    log.info("Downloader %s starting", __version__)
    return app.run()
