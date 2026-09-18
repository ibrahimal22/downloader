"""Filesystem helpers: cross-platform safe names, free space, opening files."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_component(name: str, max_len: int = 120) -> str:
    """Make one path component safe on Windows, macOS and Linux."""
    cleaned = _INVALID.sub("_", name).strip().rstrip(". ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        cleaned = "_"
    if cleaned.split(".")[0].upper() in _RESERVED:
        cleaned = f"_{cleaned}"
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip(". ")
    return cleaned


def free_space_mb(path: str | Path) -> int:
    probe = Path(path)
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return shutil.disk_usage(probe).free // (1024 * 1024)


def open_path(path: str | Path) -> None:
    """Open a file or folder with the OS default handler."""
    path = str(path)
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def reveal_in_folder(path: str | Path) -> None:
    path = Path(path)
    if sys.platform == "win32" and path.exists():
        subprocess.Popen(["explorer", "/select,", str(path)])
    elif sys.platform == "darwin" and path.exists():
        subprocess.Popen(["open", "-R", str(path)])
    else:
        open_path(path.parent if path.is_file() else path)
