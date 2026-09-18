"""User settings persisted as JSON in the app data directory."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from downloader import paths

log = logging.getLogger(__name__)


class TemplateSettings(BaseModel):
    single: str = "{uploader}/{title} [{id}].{ext}"
    playlist: str = "{uploader}/{playlist}/{playlist_index:03d} - {title}.{ext}"
    audio: str = "Music/{uploader}/{playlist|Singles}/{title}.{ext}"


class ScheduleWindow(BaseModel):
    """Weekly grid of allowed download hours. hours[day][hour]; Monday = 0."""

    enabled: bool = False
    hours: list[list[bool]] = Field(default_factory=lambda: [[True] * 24 for _ in range(7)])


class BandwidthRule(BaseModel):
    start_hour: int = 0  # inclusive
    end_hour: int = 24  # exclusive; start > end wraps past midnight
    limit_kbps: int = 0  # 0 = unlimited


class Settings(BaseModel):
    # General
    download_dir: str = Field(default_factory=lambda: str(paths.default_download_dir()))
    theme: Literal["system", "dark", "light"] = "system"
    language: str = "en"
    minimize_to_tray: bool = True
    start_with_os: bool = False
    first_run_done: bool = False
    legal_accepted: bool = False

    # Downloads
    default_preset: str = "best"
    max_concurrent: int = 3
    max_per_site: int = 2
    fragment_concurrency: int = 4
    max_retries: int = 3
    embed_thumbnail: bool = True
    embed_metadata: bool = True
    embed_chapters: bool = True
    write_info_json: bool = False  # keep .info.json next to each file
    write_nfo: bool = False  # Kodi/Jellyfin .nfo sidecar
    subtitles_enabled: bool = False
    subtitle_langs: str = "en.*"
    subtitles_auto: bool = False
    subtitles_embed: bool = True
    sponsorblock: Literal["off", "mark", "remove"] = "off"
    cookies_from_browser: str = ""  # e.g. "chrome", "firefox", "edge"
    cookies_file: str = ""
    proxy: str = ""
    min_free_space_mb: int = 1024
    skip_duplicates: bool = True
    templates: TemplateSettings = Field(default_factory=TemplateSettings)

    # Scheduling
    window: ScheduleWindow = Field(default_factory=ScheduleWindow)
    bandwidth_rules: list[BandwidthRule] = Field(default_factory=list)
    global_limit_kbps: int = 0
    pause_on_battery: bool = False
    pause_on_metered: bool = False
    after_queue_action: Literal["none", "notify", "sleep", "shutdown"] = "notify"

    # Integrations
    clipboard_monitor: bool = False
    local_server_enabled: bool = True
    local_server_port: int = 47821
    local_server_token: str = Field(default_factory=lambda: os.urandom(16).hex())

    # Updates
    auto_update_ytdlp: bool = True
    ytdlp_channel: Literal["stable", "nightly"] = "stable"
    check_app_updates: bool = True
    last_ytdlp_update: str = ""  # ISO timestamp
    last_app_update_check: str = ""
    skipped_app_version: str = ""

    # Window state
    window_geometry: str = ""  # base64 QByteArray


class SettingsStore:
    """Loads/saves Settings atomically and notifies listeners on change."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.config_file()
        self._listeners: list = []
        self.data = self._load()

    def _load(self) -> Settings:
        if not self.path.exists():
            settings = Settings()
            self._write(settings)
            return settings
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            settings = Settings.model_validate(raw)
            # Persist defaults for fields missing from older files. Otherwise random defaults
            # (like the pairing token) would change on every launch.
            if set(Settings.model_fields) - set(raw):
                self._write(settings)
            return settings
        except (json.JSONDecodeError, ValidationError) as exc:
            backup = self.path.with_suffix(".corrupt.json")
            log.error("Settings file invalid (%s); backing up to %s and using defaults", exc, backup)
            self.path.replace(backup)
            settings = Settings()
            self._write(settings)
            return settings

    def _write(self, settings: Settings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(settings.model_dump_json(indent=2))
        os.replace(tmp, self.path)

    def save(self) -> None:
        self._write(self.data)
        for cb in list(self._listeners):
            cb(self.data)

    def update(self, **changes) -> None:
        self.data = self.data.model_copy(update=changes)
        self.save()

    def subscribe(self, callback) -> None:
        self._listeners.append(callback)
