"""Engine/session setup and a helper to fetch (or create) the single profile."""
from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .models import Base, Profile


def make_session_factory(settings: Settings) -> sessionmaker:
    data_dir = Path(settings.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "jobcopilot.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    _upgrade_database(engine, db_path, data_dir)
    return sessionmaker(bind=engine, future=True)


def _upgrade_database(engine, db_path: Path, data_dir: Path) -> None:
    """Upgrade safely, recognizing databases created before Alembic existed."""
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
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
    timestamp = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"jobcopilot-{timestamp}.db"
    shutil.copy2(db_path, backup_path)


def get_or_create_profile(session: Session, settings: Settings) -> Profile:
    profile = session.execute(select(Profile)).scalars().first()
    if profile is None:
        profile = Profile(name=settings.profile.name)
        session.add(profile)
        session.commit()
        session.refresh(profile)
    return profile
