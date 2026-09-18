"""Engine/session factory and startup migrations."""

from __future__ import annotations

import logging
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from downloader import paths

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _enable_sqlite_pragmas(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


def init_db(db_path: Path | None = None) -> Engine:
    """Create the engine and bring the schema to head. Backs up the DB before migrating."""
    global _engine, _SessionLocal
    db_path = db_path or paths.db_file()
    url = f"sqlite:///{db_path.as_posix()}"
    engine = create_engine(url, future=True)
    event.listen(engine, "connect", _enable_sqlite_pragmas)

    cfg = _alembic_config(url)
    head = ScriptDirectory.from_config(cfg).get_current_head()
    with engine.connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()

    if current != head:
        if current is not None and db_path.exists():
            backup = db_path.with_suffix(f".{current}.bak")
            shutil.copy2(db_path, backup)
            log.info("Backed up database to %s before migrating", backup)
        log.info("Migrating database %s -> %s", current, head)
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "head")

    _engine = engine
    _SessionLocal = sessionmaker(engine, expire_on_commit=False)
    return engine


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("init_db() has not been called")
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    if _SessionLocal is None:
        raise RuntimeError("init_db() has not been called")
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
