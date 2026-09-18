"""Read metadata for media files: yt-dlp .info.json sidecars, falling back to ffprobe."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from downloader.core import binaries

log = logging.getLogger(__name__)

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".m4v", ".ts", ".wmv"}
AUDIO_EXTS = {".mp3", ".m4a", ".opus", ".ogg", ".flac", ".wav", ".aac", ".wma"}
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS


def is_media(path: Path) -> bool:
    return path.suffix.lower() in MEDIA_EXTS and not path.name.endswith((".part", ".ytdl"))


def iter_media(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            path = Path(dirpath) / name
            if is_media(path):
                yield path


def quick_hash(path: Path, chunk: int = 1 << 20) -> str:
    """Fast content fingerprint: size + first and last MiB."""
    size = path.stat().st_size
    h = hashlib.sha256(str(size).encode())
    with path.open("rb") as fh:
        h.update(fh.read(chunk))
        if size > chunk * 2:
            fh.seek(-chunk, os.SEEK_END)
            h.update(fh.read(chunk))
    return h.hexdigest()


def read_info_json(media: Path) -> dict[str, Any] | None:
    for candidate in (media.with_suffix(".info.json"), media.with_name(media.stem + ".info.json")):
        if candidate.is_file():
            try:
                return json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
    return None


def ffprobe(media: Path) -> dict[str, Any]:
    exe = binaries.find("ffprobe")
    if not exe:
        return {}
    try:
        proc = subprocess.run(
            [str(exe), "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams",
             str(media)],
            capture_output=True, timeout=60, creationflags=binaries.NO_WINDOW,
        )
        data = json.loads(proc.stdout.decode("utf-8", errors="replace") or "{}")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        log.debug("ffprobe failed for %s: %s", media, exc)
        return {}
    fmt = data.get("format") or {}
    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    result: dict[str, Any] = {
        "title": tags.get("title") or media.stem,
        "uploader": tags.get("artist") or tags.get("album_artist"),
        "description": tags.get("description") or tags.get("comment") or tags.get("synopsis"),
        "webpage_url": tags.get("purl"),
        "duration": float(fmt["duration"]) if fmt.get("duration") else None,
        "is_audio": video is None,
    }
    if video:
        result["width"], result["height"] = video.get("width"), video.get("height")
    date = tags.get("date")
    if date and date.replace("-", "").isdigit() and len(date.replace("-", "")) == 8:
        result["upload_date"] = date.replace("-", "")
    return result


def metadata_for(media: Path) -> dict[str, Any]:
    """Merged metadata for an existing file on disk."""
    info = read_info_json(media)
    if info:
        return {
            "title": info.get("title") or media.stem,
            "uploader": info.get("uploader") or info.get("channel"),
            "playlist_title": info.get("playlist_title") or info.get("playlist"),
            "extractor_key": info.get("extractor_key"),
            "id": info.get("id"),
            "webpage_url": info.get("webpage_url"),
            "description": info.get("description"),
            "tags": info.get("tags"),
            "duration": info.get("duration"),
            "width": info.get("width"),
            "height": info.get("height"),
            "upload_date": info.get("upload_date"),
            "is_audio": media.suffix.lower() in AUDIO_EXTS,
        }
    probed = ffprobe(media)
    probed.setdefault("title", media.stem)
    probed.setdefault("is_audio", media.suffix.lower() in AUDIO_EXTS)
    return probed
