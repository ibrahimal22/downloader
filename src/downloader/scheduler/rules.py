"""Pure scheduling rules: download windows and time-based bandwidth limits."""

from __future__ import annotations

from datetime import datetime, timedelta

from downloader.config.settings import BandwidthRule, ScheduleWindow


def in_window(window: ScheduleWindow, when: datetime) -> bool:
    if not window.enabled:
        return True
    return bool(window.hours[when.weekday()][when.hour])


def next_window_start(window: ScheduleWindow, when: datetime) -> datetime | None:
    """First hour boundary at/after `when` that is inside the window (None if never)."""
    if not window.enabled:
        return when
    if in_window(window, when):
        return when
    probe = when.replace(minute=0, second=0, microsecond=0)
    for _ in range(7 * 24):
        probe += timedelta(hours=1)
        if window.hours[probe.weekday()][probe.hour]:
            return probe
    return None


def _rule_matches(rule: BandwidthRule, hour: int) -> bool:
    start, end = rule.start_hour % 24, rule.end_hour
    if end == 24 or end == 0 and start == 0:
        end = 24
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end  # wraps past midnight


def current_limit_kbps(rules: list[BandwidthRule], global_limit: int, when: datetime) -> int:
    """First matching time rule wins; otherwise the global limit. 0 = unlimited."""
    for rule in rules:
        if _rule_matches(rule, when.hour):
            return rule.limit_kbps
    return global_limit


def per_task_limit(total_kbps: int, slots: int) -> int:
    """Split a global cap across concurrent downloads (yt-dlp limits per process)."""
    if total_kbps <= 0:
        return 0
    return max(16, total_kbps // max(1, slots))
