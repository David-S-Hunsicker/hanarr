"""Engine/session setup and a helper to fetch (or create) the single profile."""
from __future__ import annotations

import datetime as dt
import shutil
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .models import Base, Profile, utc_now


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
    profile = session.execute(select(Profile)).scalars().first()
    if profile is None:
        profile = Profile(name=settings.profile.name)
        session.add(profile)
        session.commit()
        session.refresh(profile)
    return profile
