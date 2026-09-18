"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-18
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

STATUS = sa.Enum(
    "QUEUED", "SCHEDULED", "WAITING", "DOWNLOADING", "PROCESSING", "PAUSED",
    "COMPLETED", "FAILED", "CANCELED",
    name="status", native_enum=False, length=16,
)


def upgrade() -> None:
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("preset", sa.String(64), nullable=False),
        sa.Column("output_root", sa.Text, nullable=False),
        sa.Column("interval_minutes", sa.Integer, nullable=False),
        sa.Column("filters", sa.JSON, nullable=False),
        sa.Column("keep_last", sa.Integer, nullable=False),
        sa.Column("from_now_on", sa.Boolean, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False),
        sa.Column("last_checked", sa.DateTime),
        sa.Column("last_error", sa.Text),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "downloads",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("title", sa.Text),
        sa.Column("uploader", sa.Text),
        sa.Column("extractor", sa.String(64)),
        sa.Column("video_id", sa.String(128), index=True),
        sa.Column("duration", sa.Float),
        sa.Column("thumbnail_url", sa.Text),
        sa.Column("playlist_title", sa.Text),
        sa.Column("playlist_index", sa.Integer),
        sa.Column("preset", sa.String(64), nullable=False),
        sa.Column("output_root", sa.Text, nullable=False),
        sa.Column("template_kind", sa.String(16), nullable=False),
        sa.Column("extra_args", sa.JSON, nullable=False),
        sa.Column("status", STATUS, nullable=False, index=True),
        sa.Column("priority", sa.Integer, nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("respect_window", sa.Boolean, nullable=False),
        sa.Column("scheduled_at", sa.DateTime),
        sa.Column("progress", sa.Float, nullable=False),
        sa.Column("downloaded_bytes", sa.Integer, nullable=False),
        sa.Column("total_bytes", sa.Integer),
        sa.Column("stage", sa.String(64)),
        sa.Column("filepath", sa.Text),
        sa.Column("error", sa.Text),
        sa.Column("retries", sa.Integer, nullable=False),
        sa.Column(
            "subscription_id", sa.Integer,
            sa.ForeignKey("subscriptions.id", ondelete="SET NULL"), index=True,
        ),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime),
        sa.Column("finished_at", sa.DateTime),
    )

    op.create_table(
        "library",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("filepath", sa.Text, nullable=False, unique=True),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("uploader", sa.Text),
        sa.Column("playlist_title", sa.Text),
        sa.Column("extractor", sa.String(64)),
        sa.Column("video_id", sa.String(128), index=True),
        sa.Column("source_url", sa.Text),
        sa.Column("description", sa.Text),
        sa.Column("tags", sa.Text),
        sa.Column("duration", sa.Float),
        sa.Column("width", sa.Integer),
        sa.Column("height", sa.Integer),
        sa.Column("size_bytes", sa.Integer),
        sa.Column("is_audio", sa.Boolean, nullable=False),
        sa.Column("upload_date", sa.String(8)),
        sa.Column("thumbnail_path", sa.Text),
        sa.Column("file_hash", sa.String(64), index=True),
        sa.Column("missing", sa.Boolean, nullable=False),
        sa.Column(
            "subscription_id", sa.Integer,
            sa.ForeignKey("subscriptions.id", ondelete="SET NULL"), index=True,
        ),
        sa.Column("added_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
    )

    # External-content FTS5 index over the library, kept in sync by triggers.
    op.execute(
        "CREATE VIRTUAL TABLE library_fts USING fts5("
        "title, uploader, playlist_title, description, tags, "
        "content='library', content_rowid='id', tokenize='unicode61 remove_diacritics 2')"
    )
    cols = "title, uploader, playlist_title, description, tags"
    new_cols = "new.title, new.uploader, new.playlist_title, new.description, new.tags"
    old_cols = "old.title, old.uploader, old.playlist_title, old.description, old.tags"
    op.execute(
        f"CREATE TRIGGER library_ai AFTER INSERT ON library BEGIN "
        f"INSERT INTO library_fts(rowid, {cols}) VALUES (new.id, {new_cols}); END"
    )
    op.execute(
        f"CREATE TRIGGER library_ad AFTER DELETE ON library BEGIN "
        f"INSERT INTO library_fts(library_fts, rowid, {cols}) "
        f"VALUES ('delete', old.id, {old_cols}); END"
    )
    op.execute(
        f"CREATE TRIGGER library_au AFTER UPDATE ON library BEGIN "
        f"INSERT INTO library_fts(library_fts, rowid, {cols}) "
        f"VALUES ('delete', old.id, {old_cols}); "
        f"INSERT INTO library_fts(rowid, {cols}) VALUES (new.id, {new_cols}); END"
    )


def downgrade() -> None:
    for trig in ("library_ai", "library_ad", "library_au"):
        op.execute(f"DROP TRIGGER IF EXISTS {trig}")
    op.execute("DROP TABLE IF EXISTS library_fts")
    op.drop_table("library")
    op.drop_table("downloads")
    op.drop_table("subscriptions")
