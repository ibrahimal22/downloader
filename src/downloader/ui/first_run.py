"""First-run setup: legal notice, download folder, and fetching the sidecar components."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from downloader.config.settings import SettingsStore
from downloader.core import binaries
from downloader.core.workers import run_async
from downloader.ui.icons import app_icon
from downloader.ui.widgets.common import hbox, primary_button

LEGAL = (
    "Downloader uses yt-dlp to save videos from YouTube and 1,800+ other sites. "
    "Only download content you own or have permission to download, and follow each site's "
    "terms of service and your local copyright law. DRM-protected streams are not supported."
)


class FirstRunDialog(QDialog):
    def __init__(self, settings: SettingsStore, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Welcome to Downloader")
        self.setWindowIcon(app_icon())
        self.setMinimumWidth(560)
        s = settings.data

        title = QLabel("Welcome to Downloader")
        title.setObjectName("PageTitle")
        legal = QLabel(LEGAL)
        legal.setWordWrap(True)
        self.accept_box = QCheckBox("I understand and will only download content I'm allowed to")
        self.accept_box.setChecked(s.legal_accepted)
        self.accept_box.toggled.connect(self._update)

        self.folder = QLineEdit(s.download_dir)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)

        self.components = QLabel()
        self.components.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.retry_btn = QPushButton("Retry download")
        self.retry_btn.clicked.connect(self.install_components)
        self.retry_btn.setVisible(False)

        self.continue_btn = primary_button("Get started", self._finish)

        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        lay.addWidget(title)
        lay.addWidget(legal)
        lay.addWidget(self.accept_box)
        lay.addWidget(QLabel("Save downloads to:"))
        lay.addLayout(hbox(self.folder, browse))
        lay.addWidget(QLabel("Components (yt-dlp, FFmpeg, Deno):"))
        lay.addWidget(self.components)
        lay.addWidget(self.progress)
        lay.addLayout(hbox(self.retry_btn, None, self.continue_btn))

        self._installing = False
        self._missing = binaries.missing(s.ytdlp_channel)
        if self._missing:
            self.install_components()
        else:
            self.components.setText("✔ All components are installed.")
        self._update()

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Download folder", self.folder.text())
        if folder:
            self.folder.setText(folder)

    def install_components(self) -> None:
        self._installing = True
        self.retry_btn.setVisible(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.components.setText(f"Downloading {', '.join(self._missing)} (verified with SHA-256)…")
        channel = self.settings.data.ytdlp_channel

        def work(report) -> list[str]:
            binaries.ensure_all(channel, progress=lambda n, d, t: report((n, d, t)))
            return binaries.missing(channel)

        def on_progress(value) -> None:
            name, done, total = value
            if total:
                self.progress.setRange(0, total)
                self.progress.setValue(done)
            self.progress.setFormat(f"{name}: {done >> 20} / {(total or 0) >> 20} MB")

        def on_done(missing: list[str]) -> None:
            self._installing = False
            self._missing = missing
            self.progress.setVisible(False)
            if missing:
                self.components.setText(f"Still missing: {', '.join(missing)}")
                self.retry_btn.setVisible(True)
            else:
                self.components.setText("✔ All components are installed.")
            self._update()

        def on_error(exc: Exception) -> None:
            self._installing = False
            self.progress.setVisible(False)
            self.components.setText(f"Download failed: {exc}\nCheck your internet connection.")
            self.retry_btn.setVisible(True)
            self._update()

        run_async(work, on_done=on_done, on_error=on_error, on_progress=on_progress)
        self._update()

    def _update(self) -> None:
        self.continue_btn.setEnabled(self.accept_box.isChecked() and not self._installing)

    def _finish(self) -> None:
        if self._missing and QMessageBox.question(
            self, "Components missing",
            "Some components are missing, so downloads will not work until they are installed. "
            "Continue anyway?") != QMessageBox.StandardButton.Yes:
            return
        self.settings.update(download_dir=self.folder.text().strip() or
                             self.settings.data.download_dir,
                             legal_accepted=True, first_run_done=True)
        self.accept()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape and self._installing:
            return
        super().keyPressEvent(event)
