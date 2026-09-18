"""Locate, download, verify and update the sidecar binaries (yt-dlp, ffmpeg, deno).

Binaries live in paths.bin_dir() so they can be updated without reinstalling the app.
Lookup order: user bin dir -> bundled with the installer -> system PATH.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from downloader import paths

log = logging.getLogger(__name__)

ProgressCb = Callable[[str, int, int | None], None]  # (name, done_bytes, total_bytes)

IS_WIN = sys.platform == "win32"
EXE = ".exe" if IS_WIN else ""
# Hide console windows for child processes on Windows.
NO_WINDOW = subprocess.CREATE_NO_WINDOW if IS_WIN else 0


class BinaryError(RuntimeError):
    pass


@dataclass(frozen=True)
class BinarySpec:
    name: str
    exe_names: tuple[str, ...]  # files that must exist for the binary to be usable
    url: str
    checksum_url: str | None
    archive_member_prefix: str | None = None  # for zips: extract files ending with exe_names


def _platform_key() -> str:
    machine = platform.machine().lower()
    arm = machine in ("arm64", "aarch64")
    if IS_WIN:
        return "win-arm64" if arm else "win64"
    if sys.platform == "darwin":
        return "mac"
    return "linux-arm64" if arm else "linux64"


def specs(ytdlp_channel: str = "stable") -> dict[str, BinarySpec]:
    key = _platform_key()
    yt_repo = "yt-dlp/yt-dlp" if ytdlp_channel == "stable" else "yt-dlp/yt-dlp-nightly-builds"
    yt_asset = {
        "win64": "yt-dlp.exe",
        "win-arm64": "yt-dlp_arm64.exe",
        "mac": "yt-dlp_macos",
        "linux64": "yt-dlp_linux",
        "linux-arm64": "yt-dlp_linux_aarch64",
    }[key]
    yt_base = f"https://github.com/{yt_repo}/releases/latest/download"

    # LGPL ffmpeg builds keep the whole distribution license-compatible.
    ff_asset = {
        "win64": "ffmpeg-master-latest-win64-lgpl.zip",
        "win-arm64": "ffmpeg-master-latest-winarm64-lgpl.zip",
        "linux64": "ffmpeg-master-latest-linux64-lgpl.tar.xz",
        "linux-arm64": "ffmpeg-master-latest-linuxarm64-lgpl.tar.xz",
        "mac": "",
    }[key]
    # yt-dlp/FFmpeg-Builds only ships GPL for Windows now; BtbN (its upstream) still has LGPL.
    ff_base = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest"

    deno_asset = {
        "win64": "deno-x86_64-pc-windows-msvc.zip",
        "win-arm64": "deno-aarch64-pc-windows-msvc.zip",
        "mac": "deno-aarch64-apple-darwin.zip"
        if platform.machine() == "arm64"
        else "deno-x86_64-apple-darwin.zip",
        "linux64": "deno-x86_64-unknown-linux-gnu.zip",
        "linux-arm64": "deno-aarch64-unknown-linux-gnu.zip",
    }[key]
    deno_base = "https://github.com/denoland/deno/releases/latest/download"

    result = {
        "yt-dlp": BinarySpec(
            name="yt-dlp",
            exe_names=(f"yt-dlp{EXE}",),
            url=f"{yt_base}/{yt_asset}",
            checksum_url=f"{yt_base}/SHA2-256SUMS",
        ),
        "deno": BinarySpec(
            name="deno",
            exe_names=(f"deno{EXE}",),
            url=f"{deno_base}/{deno_asset}",
            checksum_url=f"{deno_base}/{deno_asset}.sha256sum",
            archive_member_prefix="",
        ),
    }
    if ff_asset:
        result["ffmpeg"] = BinarySpec(
            name="ffmpeg",
            exe_names=(f"ffmpeg{EXE}", f"ffprobe{EXE}"),
            url=f"{ff_base}/{ff_asset}",
            checksum_url=f"{ff_base}/checksums.sha256",
            archive_member_prefix="bin/",
        )
    return result


def _bundled_dir() -> Path:
    return paths.resource_dir() / "bin"


def find(exe: str) -> Path | None:
    """Return the path to an executable (e.g. 'yt-dlp', 'ffmpeg'), or None."""
    filename = f"{exe}{EXE}"
    for directory in (paths.bin_dir(), _bundled_dir()):
        candidate = directory / filename
        if candidate.is_file():
            return candidate
    found = shutil.which(exe)
    return Path(found) if found else None


def missing(ytdlp_channel: str = "stable") -> list[str]:
    return [
        name
        for name, spec in specs(ytdlp_channel).items()
        if any(find(Path(e).stem) is None for e in spec.exe_names)
    ]


def _expected_sha256(client: httpx.Client, spec: BinarySpec, asset_name: str) -> str | None:
    if not spec.checksum_url:
        return None
    resp = client.get(spec.checksum_url, follow_redirects=True, timeout=30)
    resp.raise_for_status()
    return parse_checksum_file(resp.text, asset_name)


_HEX64 = re.compile(r"\b[0-9a-fA-F]{64}\b")


def parse_checksum_file(text: str, asset_name: str) -> str:
    """Extract the SHA256 for `asset_name`.

    Handles `sha256sum` style ("<hash>  <name>", one or many lines), a bare hash, and
    PowerShell Get-FileHash output ("Hash : <HASH>" / "Path : ...\\<name>").
    """
    hashes: list[str] = []
    for line in text.splitlines():
        match = _HEX64.search(line)
        if not match:
            continue
        hashes.append(match.group(0).lower())
        rest = line[match.end():].strip().lstrip("*")
        if rest and rest.replace("\\", "/").rsplit("/", 1)[-1] == asset_name:
            return match.group(0).lower()
    # Single-asset checksum files (bare hash or Get-FileHash) carry exactly one hash.
    if len(hashes) == 1:
        return hashes[0]
    raise BinaryError(f"No checksum for {asset_name}")


def _download(client: httpx.Client, spec: BinarySpec, progress: ProgressCb | None) -> bytes:
    buf = io.BytesIO()
    with client.stream("GET", spec.url, follow_redirects=True, timeout=60) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0)) or None
        for chunk in resp.iter_bytes(1 << 16):
            buf.write(chunk)
            if progress:
                progress(spec.name, buf.tell(), total)
    return buf.getvalue()


def _make_executable(path: Path) -> None:
    if not IS_WIN:
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _extract(data: bytes, spec: BinarySpec, dest: Path) -> None:
    asset = spec.url.rsplit("/", 1)[-1]
    wanted = set(spec.exe_names)
    written: set[str] = set()
    if asset.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for member in zf.namelist():
                base = member.rsplit("/", 1)[-1]
                if base in wanted and (spec.archive_member_prefix or "") in member:
                    target = dest / base
                    tmp = target.with_suffix(target.suffix + ".new")
                    tmp.write_bytes(zf.read(member))
                    os.replace(tmp, target)
                    _make_executable(target)
                    written.add(base)
    elif asset.endswith(".tar.xz"):
        import tarfile

        with tarfile.open(fileobj=io.BytesIO(data), mode="r:xz") as tf:
            for member in tf.getmembers():
                base = member.name.rsplit("/", 1)[-1]
                if base in wanted and member.isfile():
                    fh = tf.extractfile(member)
                    assert fh is not None
                    target = dest / base
                    target.write_bytes(fh.read())
                    _make_executable(target)
                    written.add(base)
    else:
        target = dest / spec.exe_names[0]
        tmp = target.with_suffix(target.suffix + ".new")
        tmp.write_bytes(data)
        os.replace(tmp, target)
        _make_executable(target)
        written.add(target.name)

    if written != wanted:
        raise BinaryError(f"{spec.name}: archive missing {sorted(wanted - written)}")


def install(name: str, ytdlp_channel: str = "stable", progress: ProgressCb | None = None) -> Path:
    """Download + verify + install one binary into the user bin dir."""
    spec = specs(ytdlp_channel)[name]
    asset = spec.url.rsplit("/", 1)[-1]
    log.info("Installing %s from %s", name, spec.url)
    with httpx.Client(headers={"User-Agent": "Downloader"}) as client:
        expected = _expected_sha256(client, spec, asset)
        data = _download(client, spec, progress)
    actual = hashlib.sha256(data).hexdigest()
    if expected and actual != expected:
        raise BinaryError(f"{name}: checksum mismatch (expected {expected}, got {actual})")
    _extract(data, spec, paths.bin_dir())
    return paths.bin_dir() / spec.exe_names[0]


def ensure_all(ytdlp_channel: str = "stable", progress: ProgressCb | None = None) -> None:
    for name in missing(ytdlp_channel):
        install(name, ytdlp_channel, progress)


def version(exe: str) -> str | None:
    path = find(exe)
    if not path:
        return None
    flag = "-version" if exe in ("ffmpeg", "ffprobe") else "--version"
    try:
        out = subprocess.run(
            [str(path), flag], capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    first = out.strip().splitlines()[0] if out.strip() else ""
    return first or None


def update_ytdlp(channel: str = "stable") -> str:
    """Self-update yt-dlp in place. Falls back to a fresh download if it isn't ours."""
    path = find("yt-dlp")
    if path and path.parent == paths.bin_dir():
        proc = subprocess.run(
            [str(path), "--update-to", channel],
            capture_output=True, text=True, timeout=300, creationflags=NO_WINDOW,
        )
        output = (proc.stdout + proc.stderr).strip()
        if proc.returncode == 0:
            return output
        log.warning("yt-dlp self-update failed (%s); reinstalling", output)
    install("yt-dlp", channel)
    return version("yt-dlp") or "installed"
