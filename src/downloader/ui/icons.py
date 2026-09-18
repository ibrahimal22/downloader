"""Icon helpers: themed Material Design icons via qtawesome, and the app icon."""

from __future__ import annotations

import qtawesome as qta
from PySide6.QtGui import QIcon

from downloader import paths
from downloader.ui import theme


def icon(name: str, color_key: str = "text") -> QIcon:
    return qta.icon(f"mdi6.{name}", color=theme.color(color_key),
                    color_disabled=theme.color("muted"))


def app_icon() -> QIcon:
    return QIcon(str(paths.resource_dir() / "ui" / "assets" / "app.svg"))
