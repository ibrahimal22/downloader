"""Library: records finished downloads, full-text search, imports, missing-file checks."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal
from sqlalchemy import func, or_, select, text

from downloader import paths
from downloader.config.settings import SettingsStore
from downloader.db.models import DownloadItem, LibraryItem
from downloader.db.session import session_scope
from downloader.library import indexer, organizer

log = logging.getLogger(__name__)


@dataclass
class Query:
    text: str = ""
    uploader: str | None = None
    playlist: str | None = None
    kind: str = "all"  # all | video | audio
    show_missing: bool = False
    sort: str = "added_desc"  # added_desc | added_asc | title | uploader | duration | size | date
    limit: int = 500
    offset: int = 0


_SORTS = {
    "added_desc": LibraryItem.added_at.desc(),
    "added_asc": LibraryItem.added_at.asc(),
    "title": LibraryItem.title.collate("NOCASE"),
    "uploader": LibraryItem.uploader.collate("NOCASE"),
    "duration": LibraryItem.duration.desc(),
    "size": LibraryItem.size_bytes.desc(),
    "date": LibraryItem.upload_date.desc(),
}


def fts_query(user_text: str) -> str | None:
    """Turn free text into a safe FTS5 prefix query: 'rust tut' -> '"rust"* "tut"*'."""
    tokens = re.findall(r"\w+", user_text, flags=re.UNICODE)
    if not tokens:
        return None
    return " ".join(f'"{t}"*' for t in tokens)


def find_thumbnail(video_id: str | None) -> str | None:
    if not video_id:
        return None
    matches = sorted(paths.thumbs_dir().glob(f"*-{glob_escape(video_id)}.jpg"))
    return str(matches[0]) if matches else None


def glob_escape(value: str) -> str:
    return re.sub(r"([\[\]*?])", r"[\1]", value)


class LibraryService(QObject):
    changed = Signal()

    def __init__(self, settings: SettingsStore, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.settings = settings

    # ------------------------------------------------------------------ ingest

    def add_from_download(self, item: DownloadItem, info: dict[str, Any]) -> LibraryItem | None:
        filepath = info.get("filepath") or item.filepath
        if not filepath or not os.path.exists(filepath):
            log.warning("Completed download #%s has no file on disk (%s)", item.id, filepath)
            return None
        vcodec = info.get("vcodec")
        record = self._upsert(
            filepath,
            title=info.get("title") or item.title or Path(filepath).stem,
            uploader=info.get("uploader") or info.get("channel") or item.uploader,
            playlist_title=item.playlist_title or info.get("playlist_title"),
            extractor=info.get("extractor_key") or item.extractor,
            video_id=info.get("id") or item.video_id,
            source_url=info.get("webpage_url") or item.url,
            description=info.get("description"),
            tags=", ".join(info.get("tags") or []) or None,
            duration=info.get("duration") or item.duration,
            width=info.get("width"),
            height=info.get("height"),
            is_audio=vcodec == "none" or Path(filepath).suffix.lower() in indexer.AUDIO_EXTS,
            upload_date=info.get("upload_date"),
            thumbnail_path=find_thumbnail(info.get("id") or item.video_id),
            subscription_id=item.subscription_id,
        )
        if self.settings.data.write_nfo:
            try:
                organizer.write_nfo(filepath, {**info, "playlist_title": item.playlist_title})
            except OSError as exc:
                log.warning("Could not write NFO for %s: %s", filepath, exc)
        self.changed.emit()
        return record

    def _upsert(self, filepath: str, **fields: Any) -> LibraryItem:
        path = Path(filepath)
        try:
            fields["size_bytes"] = path.stat().st_size
            fields["file_hash"] = indexer.quick_hash(path)
        except OSError:
            pass
        with session_scope() as s:
            record = s.scalar(select(LibraryItem).where(LibraryItem.filepath == str(path)))
            if record is None:
                record = LibraryItem(filepath=str(path), missing=False, **fields)
                s.add(record)
            else:
                for key, value in fields.items():
                    if value is not None:
                        setattr(record, key, value)
                record.missing = False
            s.flush()
            s.expunge(record)
        return record

    def import_folder(self, root: str | Path, progress=None) -> int:
        """Index existing media files under `root`. Blocking; run off the UI thread."""
        root = Path(root)
        with session_scope() as s:
            known = set(s.scalars(select(LibraryItem.filepath)).all())
        files = [p for p in indexer.iter_media(root) if str(p) not in known]
        for n, media in enumerate(files, start=1):
            meta = indexer.metadata_for(media)
            self._upsert(
                str(media),
                title=meta.get("title") or media.stem,
                uploader=meta.get("uploader"),
                playlist_title=meta.get("playlist_title"),
                extractor=meta.get("extractor_key"),
                video_id=meta.get("id"),
                source_url=meta.get("webpage_url"),
                description=meta.get("description"),
                tags=", ".join(meta.get("tags") or []) or None,
                duration=meta.get("duration"),
                width=meta.get("width"),
                height=meta.get("height"),
                is_audio=bool(meta.get("is_audio")),
                upload_date=meta.get("upload_date"),
                thumbnail_path=find_thumbnail(meta.get("id")),
            )
            if progress:
                progress(n, len(files))
        if files:
            self.changed.emit()
        return len(files)

    # ------------------------------------------------------------------ queries

    def search(self, q: Query) -> list[LibraryItem]:
        stmt = select(LibraryItem)
        match = fts_query(q.text)
        if match:
            ids = select(text("rowid")).select_from(text("library_fts")).where(
                text("library_fts MATCH :m").bindparams(m=match))
            stmt = stmt.where(LibraryItem.id.in_(ids))
        if q.uploader:
            stmt = stmt.where(LibraryItem.uploader == q.uploader)
        if q.playlist:
            stmt = stmt.where(LibraryItem.playlist_title == q.playlist)
        if q.kind == "video":
            stmt = stmt.where(LibraryItem.is_audio.is_(False))
        elif q.kind == "audio":
            stmt = stmt.where(LibraryItem.is_audio.is_(True))
        if not q.show_missing:
            stmt = stmt.where(LibraryItem.missing.is_(False))
        stmt = stmt.order_by(_SORTS.get(q.sort, _SORTS["added_desc"])).limit(q.limit).offset(q.offset)
        with session_scope() as s:
            rows = list(s.scalars(stmt).all())
            s.expunge_all()
        return rows

    def facets(self) -> dict[str, list[tuple[str, int]]]:
        with session_scope() as s:
            uploaders = s.execute(
                select(LibraryItem.uploader, func.count()).where(LibraryItem.uploader.is_not(None))
                .group_by(LibraryItem.uploader).order_by(func.count().desc())
            ).all()
            playlists = s.execute(
                select(LibraryItem.playlist_title, func.count())
                .where(LibraryItem.playlist_title.is_not(None))
                .group_by(LibraryItem.playlist_title).order_by(LibraryItem.playlist_title)
            ).all()
        return {"uploaders": [tuple(r) for r in uploaders],
                "playlists": [tuple(r) for r in playlists]}

    def stats(self) -> dict[str, int]:
        with session_scope() as s:
            count, size = s.execute(
                select(func.count(), func.coalesce(func.sum(LibraryItem.size_bytes), 0))
                .where(LibraryItem.missing.is_(False))
            ).one()
        return {"count": count, "size": size}

    def is_duplicate(self, video_id: str | None, extractor: str | None = None) -> bool:
        if not video_id:
            return False
        stmt = select(LibraryItem.id).where(LibraryItem.video_id == video_id,
                                            LibraryItem.missing.is_(False))
        if extractor:
            stmt = stmt.where(or_(LibraryItem.extractor.is_(None),
                                  func.lower(LibraryItem.extractor) == extractor.lower()))
        with session_scope() as s:
            return s.scalar(stmt.limit(1)) is not None

    def duplicate_groups(self) -> list[list[LibraryItem]]:
        """Groups of library entries that are the same content (same file hash or same id)."""
        groups: list[list[LibraryItem]] = []
        with session_scope() as s:
            for column in (LibraryItem.file_hash, LibraryItem.video_id):
                keys = s.scalars(
                    select(column).where(column.is_not(None), LibraryItem.missing.is_(False))
                    .group_by(column).having(func.count() > 1)
                ).all()
                for key in keys:
                    rows = list(s.scalars(select(LibraryItem).where(column == key)).all())
                    ids = {r.id for r in rows}
                    if not any(ids == {r.id for r in g} for g in groups):
                        groups.append(rows)
            s.expunge_all()
        return groups

    # ------------------------------------------------------------------ maintenance

    def verify_files(self) -> int:
        """Flag entries whose file disappeared (or reappeared). Returns number now missing."""
        missing = 0
        with session_scope() as s:
            for record in s.scalars(select(LibraryItem)).all():
                gone = not os.path.exists(record.filepath)
                if gone != record.missing:
                    record.missing = gone
                missing += gone
        self.changed.emit()
        return missing

    def delete(self, ids: list[int], delete_files: bool = False) -> None:
        with session_scope() as s:
            for record in s.scalars(select(LibraryItem).where(LibraryItem.id.in_(ids))).all():
                if delete_files:
                    for path in (Path(record.filepath), Path(record.filepath).with_suffix(".nfo"),
                                 Path(record.filepath).with_suffix(".info.json")):
                        try:
                            path.unlink(missing_ok=True)
                        except OSError as exc:
                            log.warning("Could not delete %s: %s", path, exc)
                s.delete(record)
        self.changed.emit()

    def get(self, item_id: int) -> LibraryItem | None:
        with session_scope() as s:
            record = s.get(LibraryItem, item_id)
            if record:
                s.expunge(record)
            return record

    def items_for_subscription(self, subscription_id: int) -> list[LibraryItem]:
        with session_scope() as s:
            rows = list(s.scalars(
                select(LibraryItem).where(LibraryItem.subscription_id == subscription_id,
                                          LibraryItem.missing.is_(False))
                .order_by(LibraryItem.upload_date.desc(), LibraryItem.added_at.desc())
            ).all())
            s.expunge_all()
        return rows
