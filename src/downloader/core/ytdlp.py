"""yt-dlp adapter: argument building, output-template rendering, output parsing, probing.

Everything except probe() is pure so it can be unit-tested without a network.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from downloader import paths
from downloader.config.settings import Settings
from downloader.core import binaries, presets
from downloader.core.fsutil import sanitize_component

# ---------------------------------------------------------------- templates

_TOKEN = re.compile(r"\{([A-Za-z0-9_.,]+)(?:\|([^}:]*))?(?::([^}]+))?\}")


def render_template(template: str, known: dict[str, Any] | None = None) -> str:
    """Convert our '{field|default:fmt}' syntax into a yt-dlp output template.

    Fields present in `known` are substituted literally (sanitized); the rest are left for
    yt-dlp to fill, e.g. '{uploader}' -> '%(uploader)s', '{playlist_index:03d}' ->
    '%(playlist_index)03d', '{artist,uploader|Unknown}' -> '%(artist,uploader|Unknown)s'.
    """
    known = known or {}

    def repl(m: re.Match[str]) -> str:
        name, default, fmt = m.group(1), m.group(2), m.group(3)
        value = known.get(name)
        if value is not None and value != "":
            text = format(value, fmt) if fmt else str(value)
            return sanitize_component(text).replace("%", "%%")
        spec = fmt if fmt else "s"
        default_part = f"|{default}" if default is not None else ""
        return f"%({name}{default_part}){spec}"

    # Escape literal % in the user's template first.
    parts = template.replace("\\", "/").split("/")
    rendered = []
    for part in parts:
        pieces, last = [], 0
        for m in _TOKEN.finditer(part):
            pieces.append(part[last:m.start()].replace("%", "%%"))
            pieces.append(repl(m))
            last = m.end()
        pieces.append(part[last:].replace("%", "%%"))
        rendered.append("".join(pieces))
    return "/".join(rendered)


def preview_template(template: str, sample: dict[str, Any]) -> str:
    """Render a template fully with sample values (for the settings preview)."""

    def repl(m: re.Match[str]) -> str:
        names, default, fmt = m.group(1).split(","), m.group(2), m.group(3)
        for name in names:
            if sample.get(name) not in (None, ""):
                value = sample[name]
                try:
                    return sanitize_component(format(value, fmt) if fmt else str(value))
                except (ValueError, TypeError):
                    return sanitize_component(str(value))
        return default if default is not None else "NA"

    return "/".join(_TOKEN.sub(repl, p) for p in template.replace("\\", "/").split("/"))


# ---------------------------------------------------------------- arguments

_DL_FIELDS = "status,downloaded_bytes,total_bytes,total_bytes_estimate,speed,eta,filename," \
             "fragment_index,fragment_count"
_DONE_FIELDS = "id,title,uploader,channel,extractor_key,duration,description,tags,upload_date," \
               "webpage_url,width,height,filepath,vcodec,playlist_title,thumbnails"
_INFO_FIELDS = "id,title,uploader,channel,extractor_key,duration,thumbnail,playlist_title," \
               "format_id"


@dataclass
class JobSpec:
    """Everything needed to build a yt-dlp command for one download."""

    url: str
    output_root: str
    preset: str = "best"
    template_kind: str = "single"  # single | playlist | audio
    playlist_title: str | None = None
    playlist_index: int | None = None
    limit_kbps: int = 0
    archive_file: str | None = None
    extra_args: list[str] = field(default_factory=list)


def _template_for(spec: JobSpec, settings: Settings) -> str:
    preset = presets.get(spec.preset)
    kind = "audio" if preset.audio_only else spec.template_kind
    return getattr(settings.templates, kind, settings.templates.single)


def build_args(spec: JobSpec, settings: Settings) -> list[str]:
    """Build the full argv (excluding the yt-dlp executable itself)."""
    preset = presets.get(spec.preset)
    known = {"playlist": spec.playlist_title, "playlist_title": spec.playlist_title,
             "playlist_index": spec.playlist_index}
    template = render_template(_template_for(spec, settings), known)
    root = Path(spec.output_root)

    args: list[str] = [
        "--ignore-config", "--no-playlist", "--newline", "--progress", "--no-simulate",
        "--no-colors", "--encoding", "utf-8", "--windows-filenames", "--trim-filenames", "180",
        "--retries", "10", "--fragment-retries", "10", "--continue",
        "-N", str(max(1, settings.fragment_concurrency)),
        "-P", f"home:{root}",
        "-P", f"temp:{root / '.incomplete'}",
        "-o", template,
        # Library thumbnails always go to the app data dir.
        "--write-thumbnail", "--convert-thumbnails", "jpg",
        "-o", f"thumbnail:{paths.thumbs_dir() / '%(extractor)s-%(id)s.%(ext)s'}",
        "--progress-template", f"download:[dl] %(progress.{{{_DL_FIELDS}}})j",
        "--progress-template", "postprocess:[pp] %(progress.{status,postprocessor})j",
        "--print", f"video:[info] %(.{{{_INFO_FIELDS}}})j",
        "--print", f"after_move:[done] %(.{{{_DONE_FIELDS}}})j",
    ]
    args += list(preset.args)

    if settings.embed_metadata:
        args.append("--embed-metadata")
    if settings.embed_thumbnail:
        args.append("--embed-thumbnail")
    if settings.embed_chapters and not preset.audio_only:
        args.append("--embed-chapters")
    if settings.write_info_json:
        args += ["--write-info-json", "-o", f"infojson:{template}"]
    if settings.subtitles_enabled and not preset.audio_only:
        args += ["--write-subs", "--sub-langs", settings.subtitle_langs or "en.*"]
        if settings.subtitles_auto:
            args.append("--write-auto-subs")
        if settings.subtitles_embed:
            args.append("--embed-subs")
    if settings.sponsorblock == "mark":
        args += ["--sponsorblock-mark", "all"]
    elif settings.sponsorblock == "remove":
        args += ["--sponsorblock-remove", "sponsor,selfpromo,interaction"]
    args += cookie_proxy_args(settings)

    if spec.limit_kbps > 0:
        args += ["-r", f"{spec.limit_kbps}K"]
    if spec.archive_file:
        args += ["--download-archive", spec.archive_file]

    ffmpeg = binaries.find("ffmpeg")
    if ffmpeg:
        args += ["--ffmpeg-location", str(ffmpeg.parent)]
    deno = binaries.find("deno")
    if deno:
        args += ["--js-runtimes", f"deno:{deno}"]

    args += spec.extra_args
    args += ["--", spec.url]
    return args


def cookie_proxy_args(settings: Settings) -> list[str]:
    args: list[str] = []
    if settings.cookies_file:
        args += ["--cookies", settings.cookies_file]
    elif settings.cookies_from_browser:
        args += ["--cookies-from-browser", settings.cookies_from_browser]
    if settings.proxy:
        args += ["--proxy", settings.proxy]
    return args


# ---------------------------------------------------------------- output parsing


@dataclass
class ProgressEvent:
    status: str
    downloaded: int
    total: int | None
    speed: float | None
    eta: int | None
    filename: str | None

    @property
    def percent(self) -> float | None:
        if self.total:
            return min(100.0, self.downloaded * 100.0 / self.total)
        return None


@dataclass
class PostprocessEvent:
    status: str
    postprocessor: str


@dataclass
class InfoEvent:
    info: dict[str, Any]


@dataclass
class DoneEvent:
    info: dict[str, Any]


@dataclass
class MessageEvent:
    level: str  # "error" | "warning" | "info"
    text: str


Event = ProgressEvent | PostprocessEvent | InfoEvent | DoneEvent | MessageEvent


def _loads(payload: str) -> dict[str, Any] | None:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def parse_line(line: str) -> Event | None:
    line = line.rstrip("\r\n")
    if not line:
        return None
    if line.startswith("[dl] "):
        d = _loads(line[5:])
        if d is None:
            return None
        return ProgressEvent(
            status=d.get("status") or "downloading",
            downloaded=int(d.get("downloaded_bytes") or 0),
            total=int(d.get("total_bytes") or d.get("total_bytes_estimate") or 0) or None,
            speed=d.get("speed"),
            eta=int(d["eta"]) if d.get("eta") is not None else None,
            filename=d.get("filename"),
        )
    if line.startswith("[pp] "):
        d = _loads(line[5:])
        if d is None:
            return None
        return PostprocessEvent(d.get("status") or "", d.get("postprocessor") or "")
    if line.startswith("[info] "):
        d = _loads(line[7:])
        return InfoEvent(d) if d is not None else None
    if line.startswith("[done] "):
        d = _loads(line[7:])
        return DoneEvent(d) if d is not None else None
    if line.startswith("ERROR:"):
        return MessageEvent("error", line[6:].strip())
    if line.startswith("WARNING:"):
        return MessageEvent("warning", line[8:].strip())
    return MessageEvent("info", line)


_PERMANENT_ERRORS = (
    "unsupported url",
    "private video",
    "video unavailable",
    "this video is unavailable",
    "members-only",
    "join this channel",
    "sign in to confirm your age",
    "http error 404",
    "drm",
    "requested format is not available",
    "has been removed",
    "copyright",
    "is not a valid url",
    "no video formats found",
)


def is_retryable(error: str | None) -> bool:
    if not error:
        return True
    lowered = error.lower()
    return not any(marker in lowered for marker in _PERMANENT_ERRORS)


def friendly_error(error: str) -> str:
    lowered = error.lower()
    if "sign in to confirm" in lowered or "login" in lowered or "cookies" in lowered:
        return f"{error}\n\nTip: set 'Use cookies from browser' in Settings → Downloads."
    if "drm" in lowered:
        return "This video is DRM-protected and cannot be downloaded."
    if "unsupported url" in lowered:
        return "This site or URL is not supported."
    return error


# ---------------------------------------------------------------- probing


@dataclass
class ProbeEntry:
    url: str
    id: str | None
    title: str
    duration: float | None
    thumbnail: str | None
    uploader: str | None
    index: int


@dataclass
class ProbeResult:
    url: str
    is_playlist: bool
    title: str
    uploader: str | None
    extractor: str | None
    thumbnail: str | None
    duration: float | None
    video_id: str | None
    entries: list[ProbeEntry]
    heights: list[int]  # available video heights for single videos


class ProbeError(RuntimeError):
    pass


def _thumb(d: dict[str, Any]) -> str | None:
    if d.get("thumbnail"):
        return d["thumbnail"]
    thumbs = d.get("thumbnails") or []
    return thumbs[-1].get("url") if thumbs else None


def parse_probe(url: str, data: dict[str, Any]) -> ProbeResult:
    if data.get("_type") in ("playlist", "multi_video"):
        entries = []
        for i, e in enumerate(data.get("entries") or [], start=1):
            if not e:
                continue
            entry_url = e.get("url") or e.get("webpage_url") or e.get("original_url")
            if not entry_url:
                continue
            entries.append(ProbeEntry(
                url=entry_url, id=e.get("id"), title=e.get("title") or entry_url,
                duration=e.get("duration"), thumbnail=_thumb(e),
                uploader=e.get("uploader") or e.get("channel"),
                index=e.get("playlist_index") or i,
            ))
        return ProbeResult(
            url=url, is_playlist=True, title=data.get("title") or url,
            uploader=data.get("uploader") or data.get("channel"),
            extractor=data.get("extractor_key"), thumbnail=_thumb(data), duration=None,
            video_id=data.get("id"), entries=entries, heights=[],
        )
    heights = sorted({f["height"] for f in data.get("formats") or [] if f.get("height")},
                     reverse=True)
    return ProbeResult(
        url=data.get("webpage_url") or url, is_playlist=False, title=data.get("title") or url,
        uploader=data.get("uploader") or data.get("channel"),
        extractor=data.get("extractor_key"), thumbnail=_thumb(data),
        duration=data.get("duration"), video_id=data.get("id"), entries=[], heights=heights,
    )


def probe(url: str, settings: Settings, timeout: int = 120) -> ProbeResult:
    """Blocking metadata fetch. Call from a worker thread."""
    exe = binaries.find("yt-dlp")
    if not exe:
        raise ProbeError("yt-dlp is not installed")
    cmd = [str(exe), "--ignore-config", "-J", "--flat-playlist", "--no-warnings",
           "--encoding", "utf-8", *cookie_proxy_args(settings)]
    deno = binaries.find("deno")
    if deno:
        cmd += ["--js-runtimes", f"deno:{deno}"]
    cmd += ["--", url]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout,
                              creationflags=binaries.NO_WINDOW)
    except subprocess.TimeoutExpired as exc:
        raise ProbeError("Timed out while fetching video info") from exc
    stdout = proc.stdout.decode("utf-8", errors="replace")
    stderr = proc.stderr.decode("utf-8", errors="replace")
    if proc.returncode != 0 or not stdout.strip():
        errors = [ln[6:].strip() for ln in stderr.splitlines() if ln.startswith("ERROR:")]
        raise ProbeError(friendly_error(errors[-1] if errors else stderr.strip() or "Unknown error"))
    return parse_probe(url, json.loads(stdout))
