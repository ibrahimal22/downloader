"""App self-update from GitHub Releases, with SHA256 verification of the installer."""

from __future__ import annotations

import hashlib
import logging
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from downloader import UPDATE_REPO, __version__
from downloader.core.binaries import parse_checksum_file

log = logging.getLogger(__name__)


class UpdateError(RuntimeError):
    pass


@dataclass
class Release:
    version: str
    notes: str
    page_url: str
    installer_url: str | None
    installer_name: str | None
    checksums_url: str | None


def parse_version(text: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", text.split("-")[0])
    return tuple(int(n) for n in nums[:4]) or (0,)


def is_newer(candidate: str, current: str = __version__) -> bool:
    return parse_version(candidate) > parse_version(current)


def _installer_pattern() -> re.Pattern[str]:
    if sys.platform == "win32":
        return re.compile(r"setup.*\.exe$", re.IGNORECASE)
    if sys.platform == "darwin":
        return re.compile(r"\.dmg$", re.IGNORECASE)
    return re.compile(r"\.AppImage$", re.IGNORECASE)


def parse_release(data: dict) -> Release:
    pattern = _installer_pattern()
    installer = next((a for a in data.get("assets", []) if pattern.search(a.get("name", ""))), None)
    checksums = next((a for a in data.get("assets", [])
                      if a.get("name", "").upper().startswith("SHA256SUMS")), None)
    return Release(
        version=(data.get("tag_name") or "").lstrip("v"),
        notes=data.get("body") or "",
        page_url=data.get("html_url") or "",
        installer_url=installer.get("browser_download_url") if installer else None,
        installer_name=installer.get("name") if installer else None,
        checksums_url=checksums.get("browser_download_url") if checksums else None,
    )


def check_latest(repo: str = UPDATE_REPO) -> Release | None:
    """Return the latest release if it is newer than this build, else None. Blocking."""
    if repo.startswith("your-github-user/"):
        log.info("Update repo not configured; skipping update check")
        return None
    resp = httpx.get(f"https://api.github.com/repos/{repo}/releases/latest",
                     headers={"Accept": "application/vnd.github+json",
                              "User-Agent": f"Downloader/{__version__}"},
                     timeout=20, follow_redirects=True)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    release = parse_release(resp.json())
    return release if is_newer(release.version) else None


def download_installer(release: Release, progress=None) -> Path:
    """Download and verify the installer. Refuses unverifiable downloads."""
    if not release.installer_url or not release.installer_name:
        raise UpdateError("This release has no installer for your platform.")
    if not release.checksums_url:
        raise UpdateError("This release has no SHA256SUMS file; refusing unverified update.")
    headers = {"User-Agent": f"Downloader/{__version__}"}
    with httpx.Client(headers=headers, follow_redirects=True, timeout=60) as client:
        sums = client.get(release.checksums_url)
        sums.raise_for_status()
        expected = parse_checksum_file(sums.text, release.installer_name)
        target = Path(tempfile.gettempdir()) / release.installer_name
        digest = hashlib.sha256()
        with client.stream("GET", release.installer_url) as resp, target.open("wb") as fh:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0)) or None
            done = 0
            for chunk in resp.iter_bytes(1 << 16):
                fh.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress:
                    progress((done, total))
    if digest.hexdigest() != expected:
        target.unlink(missing_ok=True)
        raise UpdateError("Downloaded installer failed checksum verification.")
    return target


def launch_installer(path: Path) -> None:
    """Start the installer detached; the caller should then quit the app."""
    if sys.platform == "win32":
        # Inno Setup: close the running app, install silently with progress, relaunch after.
        subprocess.Popen([str(path), "/SILENT", "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS"],
                         close_fds=True)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        path.chmod(0o755)
        subprocess.Popen([str(path)], close_fds=True)
