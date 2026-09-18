from sqlalchemy import text

from downloader.db.models import DownloadItem, LibraryItem, Status
from downloader.db.session import init_db, session_scope


def test_migration_creates_schema_and_fts(db):
    with session_scope() as s:
        s.add(LibraryItem(filepath="C:/v/a.mp4", title="Learning Rust in one hour",
                          uploader="Ferris", is_audio=False, missing=False))
        s.add(LibraryItem(filepath="C:/v/b.mp4", title="Cooking pasta",
                          uploader="Chef", is_audio=False, missing=False))
    with session_scope() as s:
        rows = s.execute(text("SELECT rowid FROM library_fts WHERE library_fts MATCH 'rust'")).all()
        assert len(rows) == 1
        item = s.get(LibraryItem, rows[0][0])
        item.title = "Learning Go"
    with session_scope() as s:
        count = "SELECT count(*) FROM library_fts WHERE library_fts MATCH :q"
        assert s.execute(text(count), {"q": "rust"}).scalar() == 0
        assert s.execute(text(count), {"q": "go"}).scalar() == 1


def test_download_status_roundtrip(db):
    with session_scope() as s:
        s.add(DownloadItem(url="https://example.com/v", output_root="C:/x"))
    with session_scope() as s:
        item = s.query(DownloadItem).one()
        assert item.status is Status.QUEUED
        assert item.extra_args == []


def test_init_is_idempotent(tmp_path):
    init_db(tmp_path / "x.db").dispose()
    init_db(tmp_path / "x.db").dispose()
