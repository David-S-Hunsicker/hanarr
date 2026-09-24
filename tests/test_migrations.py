import datetime as dt
import os
import shutil
import sys
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from hanarr.config import Settings
from hanarr.db import _backup_database, make_session_factory
from hanarr.models import Base, JobPosting, Profile, ScoreSnapshot

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_existing_database_is_upgraded_without_recreating_legacy_rows(tmp_path):
    """Also exercises the jobcopilot.db -> hanarr.db filename migration: this
    deliberately creates the pre-rename filename so the same pass that
    proves legacy schema rows survive also proves the legacy filename does."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "jobcopilot.db"
    engine = create_engine(f"sqlite:///{db_path}")
    legacy_names = {"profiles", "job_postings", "seen_postings", "reminders"}
    legacy_tables = [table for table in Base.metadata.sorted_tables if table.name in legacy_names]
    Base.metadata.create_all(engine, tables=legacy_tables)

    with sessionmaker(bind=engine)() as session:
        profile = Profile(name="Existing user", created_at=dt.datetime(2025, 1, 1))
        session.add(profile)
        session.flush()
        session.add(
            JobPosting(
                profile_id=profile.id,
                source="legacy",
                external_id="job-1",
                company="Acme",
                title="Engineer",
                url="https://example.com/job-1",
                fit_score=82,
                fit_rationale="Existing rationale",
                fetched_at=dt.datetime(2025, 1, 2),
            )
        )
        session.commit()
    engine.dispose()  # release the file handle so it can be renamed on Windows

    settings = Settings(data_dir=data_dir)
    make_session_factory(settings)

    with make_session_factory(settings)() as session:
        profile = session.execute(select(Profile)).scalar_one()
        posting = session.execute(select(JobPosting)).scalar_one()
        snapshot = session.execute(select(ScoreSnapshot)).scalar_one()
        assert profile.name == "Existing user"
        assert posting.id == snapshot.job_id
        assert snapshot.fit_score == 82
        assert snapshot.fit_rationale == "Existing rationale"
        assert snapshot.trigger == "initial"
        assert snapshot.scorer_metadata_json == '{"legacy": true}'

    assert (data_dir / "hanarr.db").exists()
    assert not (data_dir / "jobcopilot.db").exists()
    backups = list((data_dir / "backups").glob("hanarr-*.db"))
    assert len(backups) == 1


def test_new_database_has_migrated_schema(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    factory = make_session_factory(settings)

    with factory() as session:
        assert session.execute(select(ScoreSnapshot)).all() == []


def test_migration_0007_adds_resume_metadata_columns_without_duplicate_error(tmp_path):
    """Regression test for a real migration bug: 0001 "freezes" the legacy
    schema by reflecting Base.metadata for the four legacy table names --
    but that means a brand-new database already gets any new Profile column
    for free via 0001 itself, since Base.metadata is the live model
    definition, not a real snapshot. 0007 (the first migration to add a
    column to one of those four tables) originally assumed it was always
    the one adding the column and crashed with "duplicate column name" on a
    fresh database. This test simulates the other, equally real case this
    fix must also handle: a database that genuinely predates 0007 and is
    missing the columns, where 0007 must still add them."""
    from sqlalchemy import text

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "hanarr.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    with engine.connect() as connection:
        # Simulate a pre-0007 database by dropping the columns 0007 owns,
        # then pinning it at the previous head so _upgrade_database treats
        # it as an existing database to upgrade, not a fresh one.
        connection.execute(text("ALTER TABLE profiles DROP COLUMN resume_original_filename"))
        connection.execute(text("ALTER TABLE profiles DROP COLUMN resume_parsed_at"))
        connection.execute(text(
            "INSERT INTO profiles (id, name, resume_text, resume_summary_json, created_at, updated_at) "
            "VALUES (1, 'Existing user', 'resume text', '{}', '2025-01-01', '2025-01-01')"
        ))
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        connection.execute(text("INSERT INTO alembic_version (version_num) VALUES ('0006')"))
        connection.commit()
    engine.dispose()

    settings = Settings(data_dir=data_dir)
    make_session_factory(settings)  # must not raise "duplicate column name"

    with make_session_factory(settings)() as session:
        profile = session.execute(select(Profile)).scalar_one()
        assert profile.name == "Existing user"
        assert profile.resume_original_filename is None


def test_migration_resolves_bundle_and_ignores_working_directory_when_frozen(tmp_path, monkeypatch):
    """Regression test for a packaged-runtime crash: PyInstaller's ``--onefile``
    build exposes ``sys._MEIPASS`` as the extracted bundle root, and ``__file__``
    no longer points into the source tree, so alembic.ini and migrations/ must be
    located via ``_MEIPASS`` rather than ``Path(__file__).resolve().parents[2]``.
    The packaged runtime also changes its working directory to the user data
    folder before this runs, so resolution must not depend on the CWD either.
    """
    bundle = tmp_path / "_MEI_fake_bundle"
    bundle.mkdir()
    shutil.copy2(REPO_ROOT / "alembic.ini", bundle / "alembic.ini")
    shutil.copytree(REPO_ROOT / "migrations", bundle / "migrations")

    unrelated_cwd = tmp_path / "user_data_cwd"
    unrelated_cwd.mkdir()
    original_cwd = os.getcwd()
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    try:
        os.chdir(unrelated_cwd)
        settings = Settings(data_dir=tmp_path / "data")
        factory = make_session_factory(settings)
        with factory() as session:
            assert session.execute(select(ScoreSnapshot)).all() == []
    finally:
        os.chdir(original_cwd)


def test_backup_names_do_not_collide_with_same_timestamp(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "hanarr.db"
    db_path.write_bytes(b"database")
    fixed_now = dt.datetime(2025, 1, 1, 0, 0, 0)
    fixed = type(
        "FixedDateTime",
        (),
        {"utcnow": staticmethod(lambda: fixed_now)},
    )
    monkeypatch.setattr("hanarr.db.dt.datetime", fixed)

    _backup_database(db_path, data_dir)
    _backup_database(db_path, data_dir)

    backups = sorted((data_dir / "backups").glob("hanarr-*.db"))
    assert len(backups) == 2
    assert {path.read_bytes() for path in backups} == {b"database"}


def test_legacy_db_filename_is_renamed_and_preserved(tmp_path):
    """jobcopilot.db from before the project was renamed to Hanarr must be
    carried forward by renaming it in place, not left behind or overwritten."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    legacy_path = data_dir / "jobcopilot.db"
    legacy_engine = create_engine(f"sqlite:///{legacy_path}")
    legacy_names = {"profiles", "job_postings", "seen_postings", "reminders"}
    legacy_tables = [table for table in Base.metadata.sorted_tables if table.name in legacy_names]
    Base.metadata.create_all(legacy_engine, tables=legacy_tables)
    with sessionmaker(bind=legacy_engine)() as session:
        session.add(Profile(name="Legacy user", created_at=dt.datetime(2025, 1, 1)))
        session.commit()
    legacy_engine.dispose()

    settings = Settings(data_dir=data_dir)
    make_session_factory(settings)

    assert not legacy_path.exists()
    with make_session_factory(settings)() as session:
        profile = session.execute(select(Profile)).scalar_one()
        assert profile.name == "Legacy user"


def test_legacy_db_filename_is_not_overwritten_if_new_name_already_exists(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    settings = Settings(data_dir=data_dir)
    make_session_factory(settings)  # creates data_dir/hanarr.db normally
    current_hanarr_db = (data_dir / "hanarr.db").read_bytes()

    # A stale legacy file reappearing (e.g. restored from an old backup)
    # must not clobber the already-migrated database.
    (data_dir / "jobcopilot.db").write_bytes(b"stale legacy bytes")
    make_session_factory(settings)

    assert (data_dir / "hanarr.db").read_bytes() == current_hanarr_db
    assert (data_dir / "jobcopilot.db").read_bytes() == b"stale legacy bytes"
