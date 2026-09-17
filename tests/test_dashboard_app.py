import datetime as dt

from fastapi.testclient import TestClient

from jobcopilot.config import Settings
from jobcopilot.dashboard.app import create_app, task_is_stuck
from jobcopilot.db import get_or_create_profile, make_session_factory
from jobcopilot.models import ApplicationStatus, JobPosting, Reminder, ReminderType, SeenPosting


def test_task_is_stuck_false_when_not_running():
    assert task_is_stuck({"running": False, "started_at": None}, 1800.0) is False
    assert task_is_stuck({"running": False, "started_at": 100.0}, 1800.0, now=99999.0) is False


def test_task_is_stuck_false_when_started_at_missing():
    assert task_is_stuck({"running": True, "started_at": None}, 60.0, now=99999.0) is False


def test_task_is_stuck_false_within_ceiling():
    assert task_is_stuck({"running": True, "started_at": 1000.0}, 1800.0, now=1100.0) is False


def test_task_is_stuck_true_past_ceiling():
    # ceiling = timeout_seconds + 30s buffer
    assert task_is_stuck({"running": True, "started_at": 1000.0}, 60.0, now=1000.0 + 60.0 + 30.0 + 1) is True


def test_task_is_stuck_false_exactly_at_ceiling():
    assert task_is_stuck({"running": True, "started_at": 1000.0}, 60.0, now=1000.0 + 60.0 + 30.0) is False


def _make_isolated_settings(tmp_path):
    settings = Settings()
    settings.data_dir = tmp_path / "data"
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.llm.provider = "none"
    return settings


def test_clear_jobs_wipes_postings_seen_and_related_reminders(tmp_path):
    settings = _make_isolated_settings(tmp_path)
    session_factory = make_session_factory(settings)

    with session_factory() as session:
        profile = get_or_create_profile(session, settings)
        session.add(
            JobPosting(
                profile_id=profile.id,
                source="test",
                external_id="1",
                company="Acme",
                title="Engineer",
                url="http://example.com",
                fit_score=80,
                status=ApplicationStatus.APPLIED,
            )
        )
        session.add(SeenPosting(profile_id=profile.id, source="test", external_id="1"))
        session.add(SeenPosting(profile_id=profile.id, source="test", external_id="2"))
        session.commit()

        job = session.query(JobPosting).filter_by(external_id="1").first()
        session.add(
            Reminder(
                profile_id=profile.id,
                job_id=job.id,
                type=ReminderType.FOLLOW_UP,
                message="test reminder",
                due_at=dt.datetime.utcnow(),
            )
        )
        session.commit()

    app = create_app(settings)
    client = TestClient(app)
    r = client.post("/jobs/clear", follow_redirects=False)
    assert r.status_code == 303

    with session_factory() as session:
        profile = get_or_create_profile(session, settings)
        assert session.query(JobPosting).filter_by(profile_id=profile.id).count() == 0
        assert session.query(SeenPosting).filter_by(profile_id=profile.id).count() == 0
        assert session.query(Reminder).filter_by(profile_id=profile.id).count() == 0


def test_clear_jobs_is_a_no_op_when_nothing_exists(tmp_path):
    settings = _make_isolated_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    r = client.post("/jobs/clear", follow_redirects=False)
    assert r.status_code == 303


def test_index_shows_considered_matched_and_per_status_counts(tmp_path):
    settings = _make_isolated_settings(tmp_path)
    session_factory = make_session_factory(settings)

    with session_factory() as session:
        profile = get_or_create_profile(session, settings)
        for i in range(5):
            session.add(SeenPosting(profile_id=profile.id, source="test", external_id=str(i)))
        session.add(
            JobPosting(
                profile_id=profile.id, source="test", external_id="0", company="C",
                title="T1", url="u", fit_score=80, status=ApplicationStatus.NEW,
            )
        )
        session.add(
            JobPosting(
                profile_id=profile.id, source="test", external_id="1", company="C",
                title="T2", url="u", fit_score=75, status=ApplicationStatus.APPLIED,
            )
        )
        session.add(
            JobPosting(
                profile_id=profile.id, source="test", external_id="2", company="C",
                title="T3", url="u", fit_score=90, status=ApplicationStatus.NEW,
            )
        )
        session.commit()

    app = create_app(settings)
    client = TestClient(app)
    r = client.get("/")

    assert r.status_code == 200
    html = r.text
    assert "5" in html and "considered" in html
    assert "3" in html and "matched" in html
    assert "new (2)" in html
    assert "applied (1)" in html
    assert "reviewed (0)" in html
    assert "All (3)" in html
