import datetime as dt

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from jobcopilot.config import Settings
from jobcopilot.db import _backup_database, make_session_factory
from jobcopilot.models import Base, JobPosting, Profile, ScoreSnapshot


def test_existing_database_is_upgraded_without_recreating_legacy_rows(tmp_path):
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

    backups = list((data_dir / "backups").glob("jobcopilot-*.db"))
    assert len(backups) == 1


def test_new_database_has_migrated_schema(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    factory = make_session_factory(settings)

    with factory() as session:
        assert session.execute(select(ScoreSnapshot)).all() == []


def test_backup_names_do_not_collide_with_same_timestamp(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "jobcopilot.db"
    db_path.write_bytes(b"database")
    fixed_now = dt.datetime(2025, 1, 1, 0, 0, 0)
    fixed = type(
        "FixedDateTime",
        (),
        {"utcnow": staticmethod(lambda: fixed_now)},
    )
    monkeypatch.setattr("jobcopilot.db.dt.datetime", fixed)

    _backup_database(db_path, data_dir)
    _backup_database(db_path, data_dir)

    backups = sorted((data_dir / "backups").glob("jobcopilot-*.db"))
    assert len(backups) == 2
    assert {path.read_bytes() for path in backups} == {b"database"}
