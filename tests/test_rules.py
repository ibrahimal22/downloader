from datetime import datetime

from downloader.config.settings import BandwidthRule, ScheduleWindow
from downloader.scheduler.rules import (
    current_limit_kbps,
    in_window,
    next_window_start,
    per_task_limit,
)

MON_2300 = datetime(2026, 9, 14, 23, 30)  # Monday


def night_window() -> ScheduleWindow:
    hours = [[h >= 1 and h < 7 for h in range(24)] for _ in range(7)]
    return ScheduleWindow(enabled=True, hours=hours)


def test_disabled_window_always_allows():
    assert in_window(ScheduleWindow(enabled=False, hours=[[False] * 24] * 7), MON_2300)


def test_night_window():
    w = night_window()
    assert not in_window(w, MON_2300)
    assert in_window(w, datetime(2026, 9, 15, 3, 0))
    assert next_window_start(w, MON_2300) == datetime(2026, 9, 15, 1, 0)


def test_next_window_start_none_when_empty():
    assert next_window_start(ScheduleWindow(enabled=True, hours=[[False] * 24] * 7), MON_2300) is None


def test_bandwidth_rules_with_wraparound():
    rules = [BandwidthRule(start_hour=9, end_hour=18, limit_kbps=500),
             BandwidthRule(start_hour=22, end_hour=6, limit_kbps=0)]
    assert current_limit_kbps(rules, 2000, datetime(2026, 9, 14, 10)) == 500
    assert current_limit_kbps(rules, 2000, datetime(2026, 9, 14, 23)) == 0
    assert current_limit_kbps(rules, 2000, datetime(2026, 9, 14, 3)) == 0
    assert current_limit_kbps(rules, 2000, datetime(2026, 9, 14, 19)) == 2000


def test_full_day_rule():
    assert current_limit_kbps([BandwidthRule(start_hour=0, end_hour=24, limit_kbps=7)], 0,
                              datetime(2026, 9, 14, 23)) == 7


def test_per_task_limit():
    assert per_task_limit(0, 3) == 0
    assert per_task_limit(900, 3) == 300
    assert per_task_limit(10, 3) == 16
