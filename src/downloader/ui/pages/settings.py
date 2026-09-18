"""Settings page. Every change is validated and saved automatically."""

from __future__ import annotations

import json
import os
import platform
import zipfile
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from downloader import __version__, paths
from downloader.config.settings import SettingsStore, TemplateSettings
from downloader.core import binaries
from downloader.core.workers import run_async
from downloader.core.ytdlp import preview_template
from downloader.integrations import system
from downloader.scheduler.service import MaintenanceScheduler
from downloader.ui.pages.add_dialog import preset_combo
from downloader.ui.widgets.common import hbox, page_header

BROWSERS = ["", "chrome", "firefox", "edge", "brave", "opera", "vivaldi", "chromium", "safari"]

SAMPLE = {
    "title": "How to Bake Bread", "uploader": "Kitchen Lab", "channel": "Kitchen Lab",
    "id": "dQ9x2Ab", "ext": "mp4", "playlist": "Baking Basics", "playlist_title": "Baking Basics",
    "playlist_index": 3, "upload_date": "20250412", "extractor": "youtube", "height": 1080,
    "resolution": "1920x1080", "artist": None, "album": None,
}

TOKENS_HELP = ("Tokens: {title} {uploader} {channel} {id} {ext} {playlist} {playlist_index:03d} "
               "{upload_date} {extractor} {height} {resolution}. Use {a|Fallback} for defaults, "
               "{a,b} to try several fields, and / for folders.")


class SettingsPage(QWidget):
    server_settings_changed = Signal()

    def __init__(self, settings: SettingsStore, maintenance: MaintenanceScheduler,
                 parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.maintenance = maintenance
        self._save_timer = QTimer(self, singleShot=True, interval=400)
        self._save_timer.timeout.connect(self.save)
        s = settings.data

        tabs = QTabWidget()
        tabs.addTab(self._general(s), "General")
        tabs.addTab(self._downloads(s), "Downloads")
        tabs.addTab(self._organization(s), "Organization")
        tabs.addTab(self._network(s), "Network")
        tabs.addTab(self._integrations(s), "Integrations")
        tabs.addTab(self._about(), "Updates && About")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.addWidget(page_header("Settings"))
        lay.addWidget(tabs, 1)

        maintenance.ytdlp_updated.connect(self._on_ytdlp_updated)
        maintenance.ytdlp_update_failed.connect(self._on_ytdlp_update_failed)

    # -------------------------------------------------------------- helpers

    def _check(self, text: str, value: bool) -> QCheckBox:
        box = QCheckBox(text)
        box.setChecked(value)
        box.toggled.connect(self._changed)
        return box

    def _spin(self, lo: int, hi: int, value: int, suffix: str = "") -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(lo, hi)
        spin.setValue(value)
        if suffix:
            spin.setSuffix(suffix)
        spin.valueChanged.connect(self._changed)
        return spin

    def _line(self, value: str, placeholder: str = "") -> QLineEdit:
        edit = QLineEdit(value)
        edit.setPlaceholderText(placeholder)
        edit.textChanged.connect(self._changed)
        return edit

    def _tab(self) -> tuple[QWidget, QFormLayout]:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(16, 16, 16, 16)
        form.setVerticalSpacing(10)
        return page, form

    def _changed(self, *_args) -> None:
        self._save_timer.start()

    # -------------------------------------------------------------- tabs

    def _general(self, s) -> QWidget:
        page, form = self._tab()
        self.download_dir = self._line(s.download_dir)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_download_dir)
        self.theme = QComboBox()
        for label, value in (("Match system", "system"), ("Dark", "dark"), ("Light", "light")):
            self.theme.addItem(label, value)
        self.theme.setCurrentIndex(self.theme.findData(s.theme))
        self.theme.currentIndexChanged.connect(self._changed)
        self.tray = self._check("Keep running in the system tray when the window is closed",
                                s.minimize_to_tray)
        self.autostart = self._check("Start automatically when I sign in (minimized)",
                                     s.start_with_os)
        self.clipboard = self._check("Watch the clipboard for video links and offer to download",
                                     s.clipboard_monitor)
        form.addRow("Download folder", hbox(self.download_dir, browse))
        form.addRow("Theme", self.theme)
        form.addRow("", self.tray)
        form.addRow("", self.autostart)
        form.addRow("", self.clipboard)
        return page

    def _downloads(self, s) -> QWidget:
        page, form = self._tab()
        self.default_preset = preset_combo(s.default_preset)
        self.default_preset.currentIndexChanged.connect(self._changed)
        self.fragments = self._spin(1, 32, s.fragment_concurrency)
        self.fragments.setToolTip("Parallel fragment downloads for HLS/DASH streams")
        self.retries = self._spin(0, 10, s.max_retries)
        self.min_free = self._spin(0, 1_000_000, s.min_free_space_mb, " MB")
        self.skip_dupes = self._check("Skip videos already in the library or queue",
                                      s.skip_duplicates)
        self.embed_thumb = self._check("Embed thumbnail", s.embed_thumbnail)
        self.embed_meta = self._check("Embed metadata (title, channel, description)",
                                      s.embed_metadata)
        self.embed_chapters = self._check("Embed chapters", s.embed_chapters)
        self.info_json = self._check("Save .info.json next to each file", s.write_info_json)
        self.nfo = self._check("Write .nfo files for Kodi / Jellyfin / Plex", s.write_nfo)
        self.subs = self._check("Download subtitles", s.subtitles_enabled)
        self.sub_langs = self._line(s.subtitle_langs, "en.*,ar,fr")
        self.sub_auto = self._check("Include auto-generated subtitles", s.subtitles_auto)
        self.sub_embed = self._check("Embed subtitles into the video", s.subtitles_embed)
        self.sponsor = QComboBox()
        for label, value in (("Off", "off"), ("Mark segments as chapters", "mark"),
                             ("Remove sponsor/self-promo segments", "remove")):
            self.sponsor.addItem(label, value)
        self.sponsor.setCurrentIndex(self.sponsor.findData(s.sponsorblock))
        self.sponsor.currentIndexChanged.connect(self._changed)

        form.addRow("Default quality", self.default_preset)
        form.addRow("Fragment threads", self.fragments)
        form.addRow("Automatic retries", self.retries)
        form.addRow("Keep free disk space", self.min_free)
        form.addRow("", self.skip_dupes)
        form.addRow("", self.embed_thumb)
        form.addRow("", self.embed_meta)
        form.addRow("", self.embed_chapters)
        form.addRow("", self.info_json)
        form.addRow("", self.nfo)
        form.addRow("", self.subs)
        form.addRow("Subtitle languages", self.sub_langs)
        form.addRow("", self.sub_auto)
        form.addRow("", self.sub_embed)
        form.addRow("SponsorBlock (YouTube)", self.sponsor)
        return page

    def _organization(self, s) -> QWidget:
        page, form = self._tab()
        self.tpl_single = self._line(s.templates.single)
        self.tpl_playlist = self._line(s.templates.playlist)
        self.tpl_audio = self._line(s.templates.audio)
        self.previews = {}
        for key, edit, label in (("single", self.tpl_single, "Single videos"),
                                 ("playlist", self.tpl_playlist, "Playlist videos"),
                                 ("audio", self.tpl_audio, "Audio")):
            preview = QLabel()
            preview.setObjectName("Muted")
            self.previews[key] = (edit, preview)
            edit.textChanged.connect(self._update_previews)
            form.addRow(label, edit)
            form.addRow("", preview)
        reset = QPushButton("Reset to defaults")
        reset.clicked.connect(self._reset_templates)
        help_label = QLabel(TOKENS_HELP)
        help_label.setObjectName("Muted")
        help_label.setWordWrap(True)
        form.addRow("", help_label)
        form.addRow("", hbox(reset, None))
        self._update_previews()
        return page

    def _network(self, s) -> QWidget:
        page, form = self._tab()
        self.cookies_browser = QComboBox()
        for name in BROWSERS:
            self.cookies_browser.addItem(name.capitalize() if name else "Don't use cookies", name)
        self.cookies_browser.setCurrentIndex(max(0, self.cookies_browser.findData(
            s.cookies_from_browser)))
        self.cookies_browser.currentIndexChanged.connect(self._changed)
        self.cookies_file = self._line(s.cookies_file, "cookies.txt (Netscape format)")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_cookies)
        self.proxy = self._line(s.proxy, "http://host:port or socks5://host:port")
        note = QLabel("Cookies let you download members-only, age-restricted or private videos "
                      "you can already watch in that browser. Close the browser first if reading "
                      "its cookies fails.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        form.addRow("Cookies from browser", self.cookies_browser)
        form.addRow("…or cookies file", hbox(self.cookies_file, browse))
        form.addRow("", note)
        form.addRow("Proxy", self.proxy)
        return page

    def _integrations(self, s) -> QWidget:
        page, form = self._tab()
        self.server_enabled = self._check("Allow the browser extension to send links",
                                          s.local_server_enabled)
        self.server_port = self._spin(1024, 65535, s.local_server_port)
        self.token = QLineEdit(s.local_server_token)
        self.token.setReadOnly(True)
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        show = QPushButton("Show")
        show.setCheckable(True)
        show.toggled.connect(lambda on: self.token.setEchoMode(
            QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.token.text()))
        regen = QPushButton("Regenerate")
        regen.clicked.connect(self._regenerate_token)
        ext_btn = QPushButton("Open extension folder")
        ext_btn.clicked.connect(self._open_extension_folder)
        steps = QLabel(
            "Install the extension: open chrome://extensions (or edge://extensions), enable "
            "Developer mode, click “Load unpacked” and pick the extension folder. In Firefox use "
            "about:debugging → “Load Temporary Add-on”. Then paste the pairing token into the "
            "extension's options.")
        steps.setObjectName("Muted")
        steps.setWordWrap(True)
        form.addRow("", self.server_enabled)
        form.addRow("Port", self.server_port)
        form.addRow("Pairing token", hbox(self.token, show, copy, regen))
        form.addRow("", hbox(ext_btn, None))
        form.addRow("", steps)
        return page

    def _about(self) -> QWidget:
        page, form = self._tab()
        self.version_labels: dict[str, QLabel] = {}
        for exe in ("yt-dlp", "ffmpeg", "deno"):
            label = QLabel("…")
            label.setWordWrap(True)
            self.version_labels[exe] = label
            form.addRow(exe, label)
        self.channel = QComboBox()
        self.channel.addItem("Stable", "stable")
        self.channel.addItem("Nightly (fixes land sooner)", "nightly")
        self.channel.setCurrentIndex(self.channel.findData(self.settings.data.ytdlp_channel))
        self.channel.currentIndexChanged.connect(self._changed)
        self.auto_update = self._check("Update yt-dlp automatically (daily)",
                                       self.settings.data.auto_update_ytdlp)
        self.update_btn = QPushButton("Update yt-dlp now")
        self.update_btn.clicked.connect(self._update_ytdlp)
        self.app_updates = self._check("Check for app updates on startup",
                                       self.settings.data.check_app_updates)
        logs = QPushButton("Open logs folder")
        logs.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(paths.logs_dir()))))
        diag = QPushButton("Export diagnostics…")
        diag.clicked.connect(self._export_diagnostics)
        form.addRow("yt-dlp channel", self.channel)
        form.addRow("", self.auto_update)
        form.addRow("", hbox(self.update_btn, None))
        form.addRow("", self.app_updates)
        form.addRow("App version", QLabel(__version__))
        form.addRow("", hbox(logs, diag, None))
        legal = QLabel("Only download content you have the right to download. Respect the terms "
                       "of the sites you use and copyright law. DRM-protected content is not "
                       "supported.")
        legal.setObjectName("Muted")
        legal.setWordWrap(True)
        form.addRow("", legal)
        self._refresh_versions()
        return page

    # -------------------------------------------------------------- actions

    def _browse_download_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Download folder", self.download_dir.text())
        if folder:
            self.download_dir.setText(folder)

    def _browse_cookies(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Cookies file", "", "Text (*.txt);;All (*)")
        if path:
            self.cookies_file.setText(path)

    def _reset_templates(self) -> None:
        defaults = TemplateSettings()
        self.tpl_single.setText(defaults.single)
        self.tpl_playlist.setText(defaults.playlist)
        self.tpl_audio.setText(defaults.audio)

    def _update_previews(self) -> None:
        for key, (edit, label) in self.previews.items():
            sample = dict(SAMPLE, ext="mp3" if key == "audio" else "mp4")
            if key != "playlist" and key != "audio":
                sample.update(playlist=None, playlist_title=None, playlist_index=None)
            label.setText("→ " + preview_template(edit.text(), sample))

    def _regenerate_token(self) -> None:
        self.token.setText(os.urandom(16).hex())
        self._changed()

    def _open_extension_folder(self) -> None:
        candidates = [paths.resource_dir().parent / "browser-extension",
                      Path(__file__).resolve().parents[4] / "browser-extension"]
        folder = next((c for c in candidates if c.is_dir()), None)
        if folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        else:
            QMessageBox.information(self, "Browser extension", "Extension folder not found.")

    def _refresh_versions(self) -> None:
        def load() -> dict[str, str]:
            return {exe: binaries.version(exe) or "not installed"
                    for exe in ("yt-dlp", "ffmpeg", "deno")}

        def show(versions: dict[str, str]) -> None:
            for exe, text in versions.items():
                self.version_labels[exe].setText(text)

        run_async(load, on_done=show)

    def _update_ytdlp(self) -> None:
        self.update_btn.setEnabled(False)
        self.update_btn.setText("Updating…")
        self.maintenance.update_ytdlp()

    def _on_ytdlp_updated(self, message: str) -> None:
        self.update_btn.setEnabled(True)
        self.update_btn.setText("Update yt-dlp now")
        self._refresh_versions()

    def _on_ytdlp_update_failed(self, error: str) -> None:
        self.update_btn.setEnabled(True)
        self.update_btn.setText("Update yt-dlp now")
        QMessageBox.warning(self, "yt-dlp update", f"Update failed:\n{error}")

    def _export_diagnostics(self) -> None:
        default = str(Path.home() / f"downloader-diagnostics-{datetime.now():%Y%m%d-%H%M}.zip")
        target, _ = QFileDialog.getSaveFileName(self, "Export diagnostics", default, "Zip (*.zip)")
        if not target:
            return
        settings = json.loads(self.settings.data.model_dump_json())
        settings["local_server_token"] = "<redacted>"
        settings["proxy"] = "<set>" if settings.get("proxy") else ""
        info = {
            "app": __version__, "python": platform.python_version(),
            "os": platform.platform(),
            "binaries": {e: binaries.version(e) for e in ("yt-dlp", "ffmpeg", "deno")},
        }
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("settings.json", json.dumps(settings, indent=2))
            zf.writestr("system.json", json.dumps(info, indent=2))
            for log_file in paths.logs_dir().glob("*.log*"):
                zf.write(log_file, f"logs/{log_file.name}")
        QMessageBox.information(self, "Export diagnostics", f"Saved to {target}")

    # -------------------------------------------------------------- save

    def save(self) -> None:
        s = self.settings.data
        old_server = (s.local_server_enabled, s.local_server_port, s.local_server_token)
        autostart = self.autostart.isChecked()
        self.settings.update(
            download_dir=self.download_dir.text().strip() or s.download_dir,
            theme=self.theme.currentData(),
            minimize_to_tray=self.tray.isChecked(),
            start_with_os=autostart,
            clipboard_monitor=self.clipboard.isChecked(),
            default_preset=self.default_preset.currentData(),
            fragment_concurrency=self.fragments.value(),
            max_retries=self.retries.value(),
            min_free_space_mb=self.min_free.value(),
            skip_duplicates=self.skip_dupes.isChecked(),
            embed_thumbnail=self.embed_thumb.isChecked(),
            embed_metadata=self.embed_meta.isChecked(),
            embed_chapters=self.embed_chapters.isChecked(),
            write_info_json=self.info_json.isChecked(),
            write_nfo=self.nfo.isChecked(),
            subtitles_enabled=self.subs.isChecked(),
            subtitle_langs=self.sub_langs.text().strip() or "en.*",
            subtitles_auto=self.sub_auto.isChecked(),
            subtitles_embed=self.sub_embed.isChecked(),
            sponsorblock=self.sponsor.currentData(),
            templates=TemplateSettings(
                single=self.tpl_single.text().strip() or TemplateSettings().single,
                playlist=self.tpl_playlist.text().strip() or TemplateSettings().playlist,
                audio=self.tpl_audio.text().strip() or TemplateSettings().audio,
            ),
            cookies_from_browser=self.cookies_browser.currentData(),
            cookies_file=self.cookies_file.text().strip(),
            proxy=self.proxy.text().strip(),
            local_server_enabled=self.server_enabled.isChecked(),
            local_server_port=self.server_port.value(),
            local_server_token=self.token.text(),
            ytdlp_channel=self.channel.currentData(),
            auto_update_ytdlp=self.auto_update.isChecked(),
            check_app_updates=self.app_updates.isChecked(),
        )
        if autostart != system.autostart_enabled():
            try:
                system.set_autostart(autostart)
            except OSError as exc:
                QMessageBox.warning(self, "Start with Windows", f"Could not change: {exc}")
        s = self.settings.data
        if old_server != (s.local_server_enabled, s.local_server_port, s.local_server_token):
            self.server_settings_changed.emit()
