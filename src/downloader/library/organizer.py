"""Sidecar metadata writers (Kodi/Jellyfin .nfo)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


def write_nfo(media_path: str | Path, info: dict[str, Any]) -> Path:
    """Write a Kodi 'movie'-style NFO next to the media file."""
    media_path = Path(media_path)
    root = ET.Element("movie")

    def add(tag: str, value: Any) -> None:
        if value not in (None, "", []):
            ET.SubElement(root, tag).text = str(value)

    add("title", info.get("title"))
    add("plot", info.get("description"))
    add("studio", info.get("uploader") or info.get("channel"))
    add("director", info.get("uploader") or info.get("channel"))
    upload_date = info.get("upload_date")
    if upload_date and len(upload_date) == 8:
        add("premiered", f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}")
        add("year", upload_date[:4])
    if info.get("duration"):
        add("runtime", int(info["duration"]) // 60)
    for tag in info.get("tags") or []:
        add("tag", tag)
    add("set", info.get("playlist_title"))
    uid = ET.SubElement(root, "uniqueid", type=(info.get("extractor_key") or "web").lower(),
                        default="true")
    uid.text = str(info.get("id") or "")
    add("trailer", info.get("webpage_url"))

    ET.indent(root)
    target = media_path.with_suffix(".nfo")
    ET.ElementTree(root).write(target, encoding="utf-8", xml_declaration=True)
    return target
