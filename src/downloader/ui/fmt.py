"""Human-friendly formatting for sizes, speeds and durations."""

from __future__ import annotations


def size(num: float | int | None) -> str:
    if not num:
        return "—"
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def speed(bps: float | None) -> str:
    if not bps:
        return ""
    return f"{size(bps)}/s"


def duration(seconds: float | int | None) -> str:
    if seconds is None or seconds < 0:
        return ""
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def eta(seconds: int | None) -> str:
    if seconds is None:
        return ""
    return duration(seconds)


def kbps(limit: int) -> str:
    if limit <= 0:
        return "Unlimited"
    return f"{limit / 1024:.1f} MB/s" if limit >= 1024 else f"{limit} KB/s"


def upload_date(value: str | None) -> str:
    if not value or len(value) != 8:
        return ""
    return f"{value[:4]}-{value[4:6]}-{value[6:]}"
