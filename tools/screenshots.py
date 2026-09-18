"""Render README screenshots of every page with demo data (no network, no real media).

Usage:  QT_QPA_PLATFORM=offscreen QT_QPA_FONTDIR=C:/Windows/Fonts python tools/screenshots.py
Output: docs/screenshots/*.png
"""

from __future__ import annotations

import os
import random
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "screenshots"
HOME = Path(tempfile.mkdtemp(prefix="downloader-shots-"))
os.environ["DOWNLOADER_HOME"] = str(HOME)
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])

from sqlalchemy import select  # noqa: E402

from downloader.config.settings import BandwidthRule, ScheduleWindow, SettingsStore  # noqa: E402
from downloader.core.download_manager import DownloadManager, NewDownload  # noqa: E402
from downloader.db.models import DownloadItem, LibraryItem, Status  # noqa: E402
from downloader.db.session import init_db, session_scope  # noqa: E402
from downloader.library.service import LibraryService  # noqa: E402
from downloader.scheduler.service import MaintenanceScheduler  # noqa: E402
from downloader.subscriptions.sync import SubscriptionService  # noqa: E402
from downloader.ui import theme  # noqa: E402
from downloader.ui.main_window import MainWindow  # noqa: E402

PAGES = ["downloads", "library", "subscriptions", "scheduler", "history", "settings"]

PALETTES = [("#ff7a59", "#7b2ff7"), ("#00c6ff", "#0072ff"), ("#f7971e", "#ffd200"),
            ("#11998e", "#38ef7d"), ("#fc466b", "#3f5efb"), ("#8e2de2", "#4a00e0"),
            ("#f953c6", "#b91d73"), ("#43cea2", "#185a9d")]

LIBRARY = [
    ("Building a Desktop App with Python and Qt", "Pixel Lab", "Python Crash Course", 1843, 1080),
    ("Designing Beautiful Dark Themes", "Pixel Lab", "Python Crash Course", 1204, 1080),
    ("Async Python Explained in 20 Minutes", "Pixel Lab", "Python Crash Course", 1211, 1440),
    ("Sunrise Hike Through the Alps", "Trail Diaries", None, 902, 2160),
    ("Camping in the Desert — Full Vlog", "Trail Diaries", None, 1540, 2160),
    ("Lo-fi Beats to Code To", "Night Owl Radio", "Late Night Mixes", 3620, None),
    ("Rainy Jazz Café Ambience", "Night Owl Radio", "Late Night Mixes", 5400, None),
    ("Homemade Sourdough, Step by Step", "Kitchen Lab", None, 1322, 1080),
]


def thumbnail(path: Path, seed: int, audio: bool) -> None:
    """Original abstract artwork used as a demo thumbnail."""
    rnd = random.Random(seed)
    img = QImage(640, 360, QImage.Format.Format_RGB32)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    a, b = PALETTES[seed % len(PALETTES)]
    grad = QLinearGradient(QPointF(0, 0), QPointF(640, 360))
    grad.setColorAt(0, QColor(a))
    grad.setColorAt(1, QColor(b))
    p.fillRect(img.rect(), grad)
    p.setPen(Qt.PenStyle.NoPen)
    for _ in range(7):
        c = QColor(255, 255, 255, rnd.randint(18, 60))
        p.setBrush(c)
        r = rnd.randint(40, 180)
        p.drawEllipse(QPointF(rnd.randint(0, 640), rnd.randint(0, 360)), r, r)
    p.setBrush(QColor(255, 255, 255, 230))
    if audio:
        for i in range(24):
            h = 40 + 120 * abs(((i * 37 + seed * 11) % 17) / 17 - 0.5) * 2
            p.drawRoundedRect(QRectF(140 + i * 15, 180 - h / 2, 9, h), 4, 4)
    else:
        p.drawEllipse(QPointF(320, 180), 52, 52)
        p.setBrush(QColor(b))
        p.drawPolygon([QPointF(305, 155), QPointF(305, 205), QPointF(347, 180)])
    p.end()
    img.save(str(path))


def seed(settings: SettingsStore, lib: LibraryService, mgr: DownloadManager,
         subs: SubscriptionService) -> None:
    media = HOME / "media"
    media.mkdir()
    for n, (title, uploader, playlist, dur, height) in enumerate(LIBRARY):
        audio = height is None
        f = media / f"item{n}.{'mp3' if audio else 'mp4'}"
        f.write_bytes(os.urandom(4096))
        thumb = HOME / "thumbnails" / f"youtube-demo{n}.jpg"
        thumb.parent.mkdir(exist_ok=True)
        thumbnail(thumb, n, audio)
        lib.add_from_download(
            DownloadItem(id=0, url="https://example.com", output_root="", playlist_title=playlist),
            {"filepath": str(f), "title": title, "uploader": uploader, "id": f"demo{n}",
             "extractor_key": "Youtube", "duration": dur, "height": height,
             "width": int(height * 16 / 9) if height else None, "vcodec": "none" if audio else "avc1",
             "upload_date": f"2026{(n % 9) + 1:02d}{(n * 3) % 27 + 1:02d}",
             "description": "Demo entry used for screenshots.",
             "webpage_url": "https://example.com"})
    # Show tidy, generic paths instead of the temp folder (which contains the user name).
    with session_scope() as s:
        for rec in s.scalars(select(LibraryItem)).all():
            parts = [p for p in (rec.uploader, rec.playlist_title) if p]
            ext = Path(rec.filepath).suffix
            rec.filepath = "\\".join([r"D:\Videos\Downloader", *parts, f"{rec.title}{ext}"])

    queue = [("Full Course: Machine Learning for Beginners", "1080", 63.0, 1_932_735_283,
              Status.DOWNLOADING, "Downloading video (1/2)"),
             ("City Timelapse in 8K", "2160", 100.0, 3_221_225_472, Status.PROCESSING,
              "Merging video and audio"),
             ("Podcast #142 — The Future of Open Source", "audio_mp3", 0, None, Status.QUEUED,
              "Queued"),
             ("Documentary: Deep Ocean Explorers", "best", 0, None, Status.WAITING,
              "Waiting for download window (Sat 01:00)"),
             ("Conference Keynote 2026", "1080", 27.0, 845_000_000, Status.PAUSED, "Paused")]
    ids, _ = mgr.add([NewDownload(url=f"https://example.com/q{n}", output_root="D:/Videos",
                                  title=t, preset=p, respect_window=True)
                      for n, (t, p, *_rest) in enumerate(queue)])
    for item_id, (_t, _p, progress, total, status, stage) in zip(ids, queue, strict=True):
        item = mgr.get(item_id)
        item.progress, item.total_bytes, item.status, item.stage = progress, total, status, stage
    later, _ = mgr.add([NewDownload(url="https://example.com/later", output_root="D:/Videos",
                                    title="Weekly Tech News Roundup",
                                    scheduled_at=datetime.now() + timedelta(hours=6))])
    mgr.get(later[0]).stage = f"Scheduled for {datetime.now() + timedelta(hours=6):%Y-%m-%d %H:%M}"

    done = [("Sunrise Hike Through the Alps", Status.COMPLETED, None),
            ("Lo-fi Beats to Code To", Status.COMPLETED, None),
            ("Members-only livestream", Status.FAILED, "Join this channel to get access"),
            ("Old tutorial (removed)", Status.FAILED, "Video unavailable")]
    ids, _ = mgr.add([NewDownload(url=f"https://example.com/d{n}", output_root="D:/Videos",
                                  title=t, uploader="Trail Diaries") for n, (t, *_x) in
                      enumerate(done)], skip_duplicates=False)
    for item_id, (_t, status, error) in zip(ids, done, strict=True):
        item = mgr.get(item_id)
        item.status, item.error, item.finished_at = status, error, datetime.now()
        item.progress = 100.0 if status is Status.COMPLETED else 0.0
        item.stage = "Completed" if status is Status.COMPLETED else "Failed"

    now = datetime.now()
    for name, url, minutes, err in (
        ("Pixel Lab — Python Crash Course", "https://www.youtube.com/playlist?list=PL-demo", 360,
         None),
        ("Trail Diaries", "https://www.youtube.com/@traildiaries", 1440, None),
        ("Night Owl Radio", "https://www.youtube.com/@nightowlradio", 180, None),
    ):
        sub = subs.create(name, url, interval_minutes=minutes, keep_last=20 if minutes == 180 else 0)
        subs.update(sub.id, last_checked=now - timedelta(minutes=37), last_error=err)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    settings = SettingsStore()
    settings.update(
        first_run_done=True, legal_accepted=True, download_dir=r"D:\Videos\Downloader",
        window=ScheduleWindow(enabled=True, hours=[[d >= 5 or 1 <= h < 7 for h in range(24)]
                                                    for d in range(7)]),
        bandwidth_rules=[BandwidthRule(start_hour=9, end_hour=18, limit_kbps=2048)])
    init_db()
    lib = LibraryService(settings)
    mgr = DownloadManager(settings, is_duplicate=lib.is_duplicate)
    mgr.tick = lambda: None  # never start real processes for the demo queue
    subs = SubscriptionService(settings, mgr, lib)
    seed(settings, lib, mgr, subs)

    for mode in ("dark", "light"):
        theme.apply(app, mode)
        app.setFont(QFont(app.font().family(), 10))
        win = MainWindow(settings, mgr, lib, subs, MaintenanceScheduler(settings, subs, lib))
        mgr.stats.emit({"active": 2, "queued": 4, "speed": 7_340_032, "limit_kbps": 2048,
                        "blocked_reason": None, "paused_all": False})
        win.tray.hide()
        win.resize(1360, 820)
        win.show()
        shots = PAGES if mode == "dark" else ["downloads", "library"]

        def shoot(i: int = 0, win=win, shots=shots, mode=mode) -> None:
            if i:
                win.grab().save(str(OUT / f"{shots[i - 1]}-{mode}.png"))
            if i == len(shots):
                win.close()
                app.quit()
                return
            win.nav.setCurrentRow(PAGES.index(shots[i]))
            win.downloads_page.model.refresh()
            win.history_page.model.refresh()
            if shots[i] == "library":
                win.library_page.view.setCurrentIndex(win.library_page.model.index(0, 0))
            QTimer.singleShot(600, lambda: shoot(i + 1))

        QTimer.singleShot(300, shoot)
        app.exec()
        win.deleteLater()
    shutil.rmtree(HOME, ignore_errors=True)
    print("Saved:", sorted(p.name for p in OUT.glob("*.png")))


if __name__ == "__main__":
    main()
