"""ORM models. Schema changes must ship with an Alembic migration in db/migrations."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Status(str, enum.Enum):
    QUEUED = "queued"
    SCHEDULED = "scheduled"  # waiting for scheduled_at
    WAITING = "waiting"  # outside download window / paused by condition
    DOWNLOADING = "downloading"
    PROCESSING = "processing"  # merging / post-processing
    PAUSED = "paused"  # paused by the user
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"

    @property
    def is_active(self) -> bool:
        return self in (Status.DOWNLOADING, Status.PROCESSING)

    @property
    def is_finished(self) -> bool:
        return self in (Status.COMPLETED, Status.FAILED, Status.CANCELED)


class DownloadItem(Base):
    __tablename__ = "downloads"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    uploader: Mapped[str | None] = mapped_column(Text)
    extractor: Mapped[str | None] = mapped_column(String(64))
    video_id: Mapped[str | None] = mapped_column(String(128), index=True)
    duration: Mapped[float | None] = mapped_column(Float)
    thumbnail_url: Mapped[str | None] = mapped_column(Text)
    playlist_title: Mapped[str | None] = mapped_column(Text)
    playlist_index: Mapped[int | None] = mapped_column(Integer)

    preset: Mapped[str] = mapped_column(String(64), default="best")
    output_root: Mapped[str] = mapped_column(Text)
    template_kind: Mapped[str] = mapped_column(String(16), default="single")
    extra_args: Mapped[list] = mapped_column(JSON, default=list)

    status: Mapped[Status] = mapped_column(
        Enum(Status, native_enum=False, length=16), default=Status.QUEUED, index=True
    )
    priority: Mapped[int] = mapped_column(Integer, default=0)  # higher first
    position: Mapped[int] = mapped_column(Integer, default=0)  # manual ordering
    respect_window: Mapped[bool] = mapped_column(default=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime)

    progress: Mapped[float] = mapped_column(Float, default=0.0)  # 0..100
    downloaded_bytes: Mapped[int] = mapped_column(Integer, default=0)
    total_bytes: Mapped[int | None] = mapped_column(Integer)
    stage: Mapped[str | None] = mapped_column(String(64))
    filepath: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    retries: Mapped[int] = mapped_column(Integer, default=0)

    subscription_id: Mapped[int | None] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class LibraryItem(Base):
    __tablename__ = "library"

    id: Mapped[int] = mapped_column(primary_key=True)
    filepath: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(Text)
    uploader: Mapped[str | None] = mapped_column(Text)
    playlist_title: Mapped[str | None] = mapped_column(Text)
    extractor: Mapped[str | None] = mapped_column(String(64))
    video_id: Mapped[str | None] = mapped_column(String(128), index=True)
    source_url: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[str | None] = mapped_column(Text)  # comma separated, indexed by FTS
    duration: Mapped[float | None] = mapped_column(Float)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    is_audio: Mapped[bool] = mapped_column(default=False)
    upload_date: Mapped[str | None] = mapped_column(String(8))  # YYYYMMDD as yt-dlp gives it
    thumbnail_path: Mapped[str | None] = mapped_column(Text)
    file_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    missing: Mapped[bool] = mapped_column(default=False)
    subscription_id: Mapped[int | None] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="SET NULL"), index=True
    )
    added_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    preset: Mapped[str] = mapped_column(String(64), default="best")
    output_root: Mapped[str] = mapped_column(Text)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=360)
    filters: Mapped[dict] = mapped_column(JSON, default=dict)
    keep_last: Mapped[int] = mapped_column(Integer, default=0)  # 0 = keep all
    from_now_on: Mapped[bool] = mapped_column(default=False)
    enabled: Mapped[bool] = mapped_column(default=True)
    last_checked: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
