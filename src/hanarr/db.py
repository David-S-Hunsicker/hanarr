"""Engine/session setup and a helper to fetch (or create) the single profile."""
from __future__ import annotations

import datetime as dt
import shutil
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .models import Base, Profile, utc_now


def _set_sqlite_pragmas(dbapi_connection, connection_record) -> None:
    """A background search commits frequently (once per matched/rejected
    posting -- see pipeline.run_search_cycle) while the dashboard's own
    page loads run concurrent reads on separate connections from the same
    pool. SQLite's default rollback-journal mode lets a writer's
    transaction briefly block readers; under real contention (a slow disk,
    antivirus intercepting file I/O, a slower LLM stretching out how long
    the search runs) that can exceed sqlite3's default 5-second lock wait
    and surface as an unhandled "database is locked" error on whatever page
    the reader was loading. WAL mode is SQLite's standard fix for exactly
    this shape (readers no longer block on an in-progress writer); the
    longer busy_timeout is a second line of defense for the write side
    (two write transactions still can serialize against each other)."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


def _project_root() -> Path:
    """Resolve the repo root in a source checkout, or the PyInstaller bundle
    root when frozen. ``Path(__file__)`` points into the ``_MEIPASS`` extraction
    directory when frozen, not the source tree, so it cannot be used directly.
    """
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root is not None:
        return Path(frozen_root)
    return Path(__file__).resolve().parents[2]


def make_session_factory(settings: Settings) -> sessionmaker:
    data_dir = Path(settings.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "hanarr.db"
    _migrate_legacy_db_filename(data_dir, db_path)
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    event.listen(engine, "connect", _set_sqlite_pragmas)
    _upgrade_database(engine, db_path, data_dir)
    return sessionmaker(bind=engine, future=True)


def _migrate_legacy_db_filename(data_dir: Path, db_path: Path) -> None:
    """The database file was named jobcopilot.db before the project was
    renamed to Hanarr. Carry an existing installation's data forward by
    renaming the file in place rather than silently starting fresh — this
    only ever runs once per installation, since afterward hanarr.db exists
    and this is a no-op."""
    legacy_path = data_dir / "jobcopilot.db"
    if not db_path.exists() and legacy_path.exists():
        try:
            legacy_path.rename(db_path)
        except OSError as exc:
            raise RuntimeError(
                f"Could not rename {legacy_path} to {db_path}: {exc}. "
                f"Close any other running Hanarr/jobcopilot instance and try again."
            ) from exc


def _upgrade_database(engine, db_path: Path, data_dir: Path) -> None:
    """Upgrade safely, recognizing databases created before Alembic existed."""
    root = _project_root()
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    # Set script_location as an absolute path rather than relying on the
    # ini file's relative "migrations" value, which would otherwise resolve
    # against the current working directory (the packaged runtime changes
    # its working directory to the user data folder before this runs).
    config.set_main_option("script_location", str(root / "migrations"))
    head = ScriptDirectory.from_config(config).get_current_head()
    inspector = inspect(engine)
    has_version_table = inspector.has_table("alembic_version")
    legacy_tables = {"profiles", "job_postings", "seen_postings", "reminders"}
    has_legacy_schema = legacy_tables.issubset(set(inspector.get_table_names()))

    with engine.connect() as connection:
        current = None
        if has_version_table:
            current = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()

    if current != head:
        with engine.connect() as connection:
            # WAL mode means a recent commit can still be sitting only in
            # the -wal file, not yet folded into hanarr.db itself --
            # checkpoint first so the plain file copy _backup_database does
            # is as complete as possible rather than stale. PASSIVE (the
            # default, no argument) is deliberate here over TRUNCATE/FULL:
            # those block against a concurrent reader/writer and, tested
            # against one directly, produced a corrupted checkpoint --
            # PASSIVE never blocks or forces anything, it just folds in
            # whatever it safely can.
            connection.execute(text("PRAGMA wal_checkpoint"))
        _backup_database(db_path, data_dir)

    try:
        if not has_version_table and has_legacy_schema:
            command.stamp(config, "0001")
        command.upgrade(config, "head")
    except Exception as exc:
        raise RuntimeError(f"Database migration failed for {db_path}: {exc}") from exc


def _backup_database(db_path: Path, data_dir: Path) -> None:
    if not db_path.exists():
        return
    backup_dir = data_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = utc_now().strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = backup_dir / f"hanarr-{timestamp}.db"
    suffix = 1
    while backup_path.exists():
        backup_path = backup_dir / f"hanarr-{timestamp}-{suffix}.db"
        suffix += 1
    shutil.copy2(db_path, backup_path)


def get_or_create_profile(session: Session, settings: Settings) -> Profile:
    profile = session.execute(select(Profile).order_by(Profile.id)).scalars().first()
    if profile is None:
        profile = Profile(name=settings.profile.name)
        session.add(profile)
        session.commit()
        session.refresh(profile)
    return profile


def get_active_profile(session: Session, settings: Settings, profile_id: int | None = None) -> Profile:
    """Like get_or_create_profile, but honors an explicit profile_id when
    given (e.g. from the dashboard's active-profile cookie) -- falls back to
    the default (first-created) profile when profile_id is None or doesn't
    resolve to a real row, so every existing single-profile caller keeps
    working unchanged."""
    if profile_id is not None:
        profile = session.get(Profile, profile_id)
        if profile is not None:
            return profile
    return get_or_create_profile(session, settings)


def list_profiles(session: Session) -> list[Profile]:
    return list(session.execute(select(Profile).order_by(Profile.id)).scalars().all())
