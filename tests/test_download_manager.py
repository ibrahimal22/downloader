import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from downloader.config.settings import ScheduleWindow, SettingsStore
from downloader.core import download_manager, download_task
from downloader.core.download_manager import DownloadManager, NewDownload
from downloader.db.models import DownloadItem, Status
from downloader.db.session import session_scope

FAKE = str(Path(__file__).parent / "fixtures" / "fake_ytdlp.py")


@pytest.fixture
def manager(db, tmp_path, monkeypatch, qtbot):
    monkeypatch.setattr(download_task.binaries, "find", lambda _exe: Path(sys.executable))
    monkeypatch.setattr(download_manager.ytdlp, "build_args",
                        lambda spec, _s: [FAKE, spec.url])
    store = SettingsStore(tmp_path / "settings.json")
    store.update(max_concurrent=2, max_per_site=5, min_free_space_mb=0, max_retries=2)
    mgr = DownloadManager(store)
    yield mgr
    mgr.shutdown()


def add(mgr, *urls, **kw):
    ids, skipped = mgr.add([NewDownload(url=u, output_root="C:/out", **kw) for u in urls])
    return ids


def wait_status(qtbot, mgr, item_id, status, timeout=10000):
    qtbot.waitUntil(lambda: mgr.get(item_id).status is status, timeout=timeout)


def test_download_completes_and_persists(manager, qtbot):
    with qtbot.waitSignal(manager.completed, timeout=10000) as sig:
        (item_id,) = add(manager, "https://fake/ok")
    assert sig.args[0] == item_id and sig.args[1]["filepath"] == "C:/out/T.mp4"
    item = manager.get(item_id)
    assert item.status is Status.COMPLETED and item.progress == 100.0
    assert item.title == "Title https://fake/ok"
    with session_scope() as s:
        assert s.get(DownloadItem, item_id).status is Status.COMPLETED


def test_permanent_failure_does_not_retry(manager, qtbot):
    with qtbot.waitSignal(manager.failed, timeout=10000) as sig:
        (item_id,) = add(manager, "https://fake/fail-permanent")
    assert "Private video" in sig.args[1]
    assert manager.get(item_id).status is Status.FAILED


def test_transient_failure_schedules_retry(manager, qtbot):
    (item_id,) = add(manager, "https://fake/fail-transient")
    wait_status(qtbot, manager, item_id, Status.SCHEDULED)
    item = manager.get(item_id)
    assert item.retries == 1
    assert item.scheduled_at > datetime.now() + timedelta(seconds=20)


def test_pause_resume_and_cancel(manager, qtbot):
    a, b = add(manager, "https://fake/slow-a", "https://fake/slow-b")
    qtbot.waitUntil(lambda: manager.get(a).progress > 0, timeout=10000)
    manager.pause([a])
    wait_status(qtbot, manager, a, Status.PAUSED)
    manager.cancel([b])
    wait_status(qtbot, manager, b, Status.CANCELED)
    manager.resume([a])
    wait_status(qtbot, manager, a, Status.COMPLETED, timeout=20000)


def test_concurrency_limit(manager, qtbot):
    manager.settings.update(max_concurrent=1)
    add(manager, "https://fake/slow-1", "https://fake/slow-2")
    qtbot.waitUntil(lambda: len(manager.tasks) == 1, timeout=5000)
    qtbot.wait(300)
    assert len(manager.tasks) == 1


def test_window_blocks_and_start_now_overrides(manager, qtbot):
    manager.settings.update(window=ScheduleWindow(enabled=True, hours=[[False] * 24] * 7))
    (item_id,) = add(manager, "https://fake/ok")
    wait_status(qtbot, manager, item_id, Status.WAITING)
    assert "download window" in manager.get(item_id).stage
    manager.start_now([item_id])
    wait_status(qtbot, manager, item_id, Status.COMPLETED)


def test_scheduled_item_waits_for_time(manager, qtbot):
    (item_id,) = add(manager, "https://fake/ok", scheduled_at=datetime.now() + timedelta(hours=1))
    qtbot.wait(300)
    assert manager.get(item_id).status is Status.SCHEDULED
    manager.schedule([item_id], datetime.now() - timedelta(seconds=1))
    manager.tick()
    wait_status(qtbot, manager, item_id, Status.COMPLETED)


def test_duplicates_skipped(manager):
    manager.is_duplicate = lambda vid, _ext: vid == "in-library"
    manager.settings.update(window=ScheduleWindow(enabled=True, hours=[[False] * 24] * 7))
    ids, skipped = manager.add([
        NewDownload(url="u1", output_root="C:/o", video_id="in-library"),
        NewDownload(url="u2", output_root="C:/o", video_id="new"),
        NewDownload(url="u3", output_root="C:/o", video_id="new"),
    ])
    assert len(ids) == 1 and skipped == 2


def test_load_recovers_interrupted_items(db, tmp_path):
    with session_scope() as s:
        s.add(DownloadItem(url="u", output_root="C:/o", status=Status.DOWNLOADING))
    mgr = DownloadManager(SettingsStore(tmp_path / "s.json"))
    mgr.load()
    (item,) = mgr.items.values()
    assert item.status is Status.QUEUED


def test_reorder_and_move(manager):
    manager.settings.update(window=ScheduleWindow(enabled=True, hours=[[False] * 24] * 7))
    a, b, c = add(manager, "u-a", "u-b", "u-c")
    manager.move([c], -2)
    assert [i.id for i in manager.ordered()] == [c, a, b]
