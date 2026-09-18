from datetime import datetime, timedelta

import pytest

from downloader.config.settings import ScheduleWindow, SettingsStore
from downloader.core.download_manager import DownloadManager
from downloader.core.ytdlp import ProbeEntry, ProbeResult
from downloader.db.models import DownloadItem
from downloader.library.service import LibraryService
from downloader.subscriptions import sync
from downloader.subscriptions.sync import (
    SubscriptionService,
    download_args,
    entry_passes,
    read_archive,
)


def entry(i, title="Video", duration=300, url=None):
    return ProbeEntry(url=url or f"https://y/watch?v={i}", id=str(i), title=title,
                      duration=duration, thumbnail=None, uploader="U", index=i)


def playlist(*entries):
    return ProbeResult(url="https://y/pl", is_playlist=True, title="PL", uploader="U",
                       extractor="YoutubeTab", thumbnail=None, duration=None, video_id="pl",
                       entries=list(entries), heights=[])


@pytest.fixture
def env(db, tmp_path, monkeypatch, qtbot):
    store = SettingsStore(tmp_path / "s.json")
    # Keep the queue from actually starting anything.
    store.update(download_dir=str(tmp_path),
                 window=ScheduleWindow(enabled=True, hours=[[False] * 24] * 7))
    library = LibraryService(store)
    manager = DownloadManager(store, is_duplicate=library.is_duplicate)
    service = SubscriptionService(store, manager, library)
    probe_result = {"value": playlist()}

    def fake_run_async(fn, on_done=None, on_error=None, on_progress=None):
        on_done(probe_result["value"])

    monkeypatch.setattr(sync, "run_async", fake_run_async)
    return service, manager, library, probe_result


def test_filters():
    assert entry_passes(entry(1), {})
    assert not entry_passes(entry(1, duration=30), {"min_duration": 60})
    assert not entry_passes(entry(1, duration=4000), {"max_duration": 3600})
    assert entry_passes(entry(1, title="Rust Tutorial"), {"include_keywords": "python, rust"})
    assert not entry_passes(entry(1, title="Vlog"), {"include_keywords": "python, rust"})
    assert not entry_passes(entry(1, title="LIVE stream"), {"exclude_keywords": "live"})
    assert not entry_passes(entry(1, url="https://y/shorts/abc"), {"skip_shorts": True})
    assert download_args({"date_after": "20250101", "skip_shorts": True}) == [
        "--dateafter", "20250101", "--match-filters", "original_url!*=/shorts/"]
    assert download_args({"date_after": "bad"}) == []


def test_check_queues_new_items_with_numbering(env):
    service, manager, _lib, probe = env
    sub = service.create("My List", "https://y/pl", filters={"min_duration": 60})
    probe["value"] = playlist(entry(1), entry(2, duration=10), entry(3))
    service.check(sub.id)
    items = manager.ordered()
    assert [i.video_id for i in items] == ["1", "3"]
    assert all(i.subscription_id == sub.id and i.template_kind == "playlist" for i in items)
    assert items[1].playlist_index == 3 and items[0].playlist_title == "My List"
    assert service.get(sub.id).last_checked is not None

    # Second check: already-queued items are not duplicated.
    service.check(sub.id)
    assert len(manager.ordered()) == 2


def test_from_now_on_archives_existing(env):
    service, manager, _lib, probe = env
    sub = service.create("Chan", "https://y/c", from_now_on=True)
    probe["value"] = playlist(entry(1), entry(2))
    service.check(sub.id)
    assert manager.ordered() == []
    assert read_archive(sub.id) == {"1", "2"}

    probe["value"] = playlist(entry(1), entry(2), entry(3))
    service.check(sub.id)
    assert [i.video_id for i in manager.ordered()] == ["3"]


def test_check_due_respects_interval(env):
    service, manager, _lib, probe = env
    sub = service.create("S", "https://y/s", interval_minutes=60)
    service.update(sub.id, last_checked=datetime.now() - timedelta(minutes=10))
    probe["value"] = playlist(entry(1))
    service.check_due()
    assert manager.ordered() == []
    service.update(sub.id, last_checked=datetime.now() - timedelta(minutes=61))
    service.check_due()
    assert len(manager.ordered()) == 1


def test_retention_keeps_last_n(env, tmp_path):
    service, _manager, library, _probe = env
    sub = service.create("R", "https://y/r", keep_last=2)
    for n, date in enumerate(["20240101", "20240201", "20240301"]):
        path = tmp_path / f"v{n}.mp4"
        path.write_bytes(bytes([n]) * 100)
        item = DownloadItem(id=n, url="u", output_root=str(tmp_path), subscription_id=sub.id)
        library.add_from_download(item, {"filepath": str(path), "title": f"V{n}",
                                         "id": f"v{n}", "upload_date": date})
    assert service.apply_retention(sub.id) == 1
    assert not (tmp_path / "v0.mp4").exists()
    assert (tmp_path / "v2.mp4").exists()
