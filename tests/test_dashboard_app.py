import datetime as dt
import threading
import time

from fastapi.testclient import TestClient

import jobcopilot.dashboard.app as app_mod
import jobcopilot.pipeline as pipeline_mod
from jobcopilot.config import Settings
from jobcopilot.connectors.base import RawJobPosting
from jobcopilot.dashboard.app import create_app, format_posting_age, is_recent_posting, task_is_stuck
from jobcopilot.db import get_or_create_profile, make_session_factory
from jobcopilot.llm.base import LLMClient
from jobcopilot.models import ApplicationStatus, JobPosting, Reminder, ReminderType, SeenPosting
from jobcopilot.ollama_setup import HardwareInfo, OllamaDiagnostics, ModelRecommendation


def test_task_is_stuck_false_when_not_running():
    assert task_is_stuck({"running": False, "started_at": None}, 1800.0) is False
    assert task_is_stuck({"running": False, "started_at": 100.0}, 1800.0, now=99999.0) is False


def test_dashboard_uses_hanarr_product_name(tmp_path):
    settings = _make_isolated_settings(tmp_path)
    app = create_app(settings)

    assert app.title == "Hanarr"
    assert "Hanarr" in TestClient(app).get("/").text


def test_provider_setup_decline_returns_offer_without_download(tmp_path, monkeypatch):
    settings = _make_isolated_settings(tmp_path)
    settings.llm.model = "qwen2.5:7b"
    diagnostics = OllamaDiagnostics(
        executable_path=None,
        executable_version=None,
        service_reachable=False,
        service_error="offline",
        installed_models=(),
        configured_model=settings.llm.model,
        configured_model_available=False,
        hardware=HardwareInfo(8, 20, "Windows"),
        recommendation=ModelRecommendation("qwen2.5:7b", "test", "low"),
    )
    monkeypatch.setattr(app_mod, "detect_ollama", lambda *args: diagnostics)
    response = TestClient(create_app(settings)).post(
        "/config/provider/setup",
        data={"action": "ollama_installer"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "consent_required"
    assert response.json()["offer"]["license_url"]
    assert not (settings.data_dir / "setup").exists()


def test_provider_setup_is_no_op_when_model_is_already_available(tmp_path, monkeypatch):
    settings = _make_isolated_settings(tmp_path)
    diagnostics = OllamaDiagnostics(
        executable_path=r"C:\Ollama\ollama.exe",
        executable_version="ollama version test",
        service_reachable=True,
        service_error=None,
        installed_models=(),
        configured_model=settings.llm.model,
        configured_model_available=True,
        hardware=HardwareInfo(8, 20, "Windows"),
        recommendation=ModelRecommendation(settings.llm.model, "test", "low"),
    )
    monkeypatch.setattr(app_mod, "detect_ollama", lambda *args: diagnostics)
    response = TestClient(create_app(settings)).post(
        "/config/provider/setup",
        data={"action": "model"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "already_available", "model": settings.llm.model}


def test_update_install_requires_explicit_approval(tmp_path):
    settings = _make_isolated_settings(tmp_path)
    client = TestClient(create_app(settings))
    response = client.post("/config/update/install", data={})
    assert response.status_code == 409
    assert response.json()["status"] == "approval_required"


def test_update_install_does_not_claim_to_install_even_after_approval(tmp_path):
    settings = _make_isolated_settings(tmp_path)
    client = TestClient(create_app(settings))
    response = client.post("/config/update/install", data={"consent": "true"})
    assert response.status_code == 501
    assert response.json()["status"] == "not_implemented"


def test_resume_upload_enforces_streamed_size_limit(tmp_path, monkeypatch):
    settings = _make_isolated_settings(tmp_path)
    settings.profile.resume_path = str(tmp_path / "resume.md")
    monkeypatch.setattr(app_mod, "DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr(app_mod, "MAX_RESUME_BYTES", 3)
    client = TestClient(create_app(settings))

    response = client.post(
        "/config/resume",
        files={"file": ("resume.txt", b"four", "text/plain")},
    )

    assert response.status_code == 413
    assert not (tmp_path / "resumes" / "resume.txt").exists()


def test_invalid_job_status_is_a_client_error(tmp_path):
    settings = _make_isolated_settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        job = JobPosting(
            profile_id=profile.id,
            source="test",
            external_id="status",
            company="Acme",
            title="Engineer",
            url="https://example.test/status",
        )
        session.add(job)
        session.commit()
        job_id = job.id

    response = TestClient(create_app(settings)).post(
        f"/jobs/{job_id}/status", data={"new_status": "not-a-status"}
    )
    assert response.status_code == 400


def test_task_is_stuck_false_when_started_at_missing():
    assert task_is_stuck({"running": True, "started_at": None}, 60.0, now=99999.0) is False


def test_task_is_stuck_false_within_ceiling():
    assert task_is_stuck({"running": True, "started_at": 1000.0}, 1800.0, now=1100.0) is False


def test_task_is_stuck_true_past_ceiling():
    # ceiling = timeout_seconds + 30s buffer
    assert task_is_stuck({"running": True, "started_at": 1000.0}, 60.0, now=1000.0 + 60.0 + 30.0 + 1) is True


def test_task_is_stuck_false_exactly_at_ceiling():
    assert task_is_stuck({"running": True, "started_at": 1000.0}, 60.0, now=1000.0 + 60.0 + 30.0) is False


def test_format_posting_age_none_when_no_date():
    assert format_posting_age(None) is None


def test_format_posting_age_today():
    now = dt.datetime(2026, 9, 17, 12, 0, 0)
    assert format_posting_age(now, now=now) == "Posted today"


def test_format_posting_age_yesterday():
    now = dt.datetime(2026, 9, 17, 12, 0, 0)
    posted = now - dt.timedelta(days=1)
    assert format_posting_age(posted, now=now) == "Posted yesterday"


def test_format_posting_age_days():
    now = dt.datetime(2026, 9, 17, 12, 0, 0)
    posted = now - dt.timedelta(days=7)
    assert format_posting_age(posted, now=now) == "Posted 7d ago"


def test_format_posting_age_months():
    now = dt.datetime(2026, 9, 17, 12, 0, 0)
    posted = now - dt.timedelta(days=45)
    assert format_posting_age(posted, now=now) == "Posted 1mo ago"


def test_format_posting_age_future_date_does_not_go_negative():
    now = dt.datetime(2026, 9, 17, 12, 0, 0)
    posted = now + dt.timedelta(hours=2)  # clock skew between sources
    assert format_posting_age(posted, now=now) == "Posted today"


def test_is_recent_posting_true_within_window():
    now = dt.datetime(2026, 9, 17, 12, 0, 0)
    assert is_recent_posting(now - dt.timedelta(days=2), now=now) is True


def test_is_recent_posting_false_outside_window():
    now = dt.datetime(2026, 9, 17, 12, 0, 0)
    assert is_recent_posting(now - dt.timedelta(days=4), now=now) is False


def test_is_recent_posting_false_when_no_date():
    assert is_recent_posting(None) is False


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


def test_clear_jobs_refused_while_search_is_running(tmp_path, monkeypatch):
    settings = _make_isolated_settings(tmp_path)
    settings.matching.min_fit_score = 0

    hang = threading.Event()

    class SlowConnector:
        name = "arbeitnow"

        def fetch(self):
            return [
                RawJobPosting(
                    source="arbeitnow", external_id="1", company="Acme", title="Engineer",
                    location="Remote", remote=True, url="http://x", description="d",
                )
            ]

    class SlowLLM(LLMClient):
        def complete_json(self, system: str, user: str) -> str:
            hang.wait(timeout=10)
            return '{"score": 80, "dealbreaker_hit": false, "fails_minimum_requirements": false, "rationale": "ok"}'

    monkeypatch.setattr(pipeline_mod, "build_enabled_connectors", lambda sources: [SlowConnector()])
    monkeypatch.setattr(app_mod, "build_llm_client", lambda cfg: SlowLLM())

    app = create_app(settings)
    client = TestClient(app)

    try:
        r = client.post("/search", follow_redirects=False)
        assert r.status_code == 303
        time.sleep(0.3)

        status = client.get("/search/status").json()
        assert status["search_running"] is True

        r2 = client.post("/jobs/clear", follow_redirects=False)
        assert r2.status_code == 409
    finally:
        hang.set()
        time.sleep(0.3)


def _extract_job_card(html: str, title: str) -> str:
    """Splits on the job-card class boundary so assertions check the
    correct posting's markup, not text bleeding in from an adjacent card."""
    for card in html.split("job-card"):
        if title in card:
            return card
    raise AssertionError(f"No job-card found containing title {title!r}")


def test_index_shows_new_badge_and_relative_age_for_recent_posting(tmp_path):
    settings = _make_isolated_settings(tmp_path)
    session_factory = make_session_factory(settings)
    now = dt.datetime.utcnow()

    with session_factory() as session:
        profile = get_or_create_profile(session, settings)
        session.add(
            JobPosting(
                profile_id=profile.id, source="test", external_id="1", company="Acme",
                title="Recent Posting Title", url="u", fit_score=80,
                status=ApplicationStatus.NEW, posted_at=now,
            )
        )
        session.commit()

    app = create_app(settings)
    client = TestClient(app)
    html = client.get("/").text

    card = _extract_job_card(html, "Recent Posting Title")
    assert "new-badge" in card
    assert "Posted today" in card


def test_index_omits_new_badge_for_old_posting(tmp_path):
    settings = _make_isolated_settings(tmp_path)
    session_factory = make_session_factory(settings)
    now = dt.datetime.utcnow()

    with session_factory() as session:
        profile = get_or_create_profile(session, settings)
        session.add(
            JobPosting(
                profile_id=profile.id, source="test", external_id="1", company="Acme",
                title="Old Posting Title", url="u", fit_score=80,
                status=ApplicationStatus.NEW, posted_at=now - dt.timedelta(days=45),
            )
        )
        session.commit()

    app = create_app(settings)
    client = TestClient(app)
    html = client.get("/").text

    card = _extract_job_card(html, "Old Posting Title")
    assert "new-badge" not in card
    assert "Posted 1mo ago" in card


def test_index_omits_age_pill_when_posted_at_is_none(tmp_path):
    settings = _make_isolated_settings(tmp_path)
    session_factory = make_session_factory(settings)

    with session_factory() as session:
        profile = get_or_create_profile(session, settings)
        session.add(
            JobPosting(
                profile_id=profile.id, source="test", external_id="1", company="Acme",
                title="No Date Posting Title", url="u", fit_score=80,
                status=ApplicationStatus.NEW, posted_at=None,
            )
        )
        session.commit()

    app = create_app(settings)
    client = TestClient(app)
    html = client.get("/").text

    card = _extract_job_card(html, "No Date Posting Title")
    assert "new-badge" not in card
    assert "Posted" not in card


def test_search_status_tracks_considered_progress_across_sources(monkeypatch, tmp_path):
    settings = _make_isolated_settings(tmp_path)
    settings.matching.min_fit_score = 60

    def make_jobs(source, n):
        return [
            RawJobPosting(
                source=source, external_id=f"{source}-{i}", company="Acme", title="Engineer",
                location="Remote", remote=True, url="http://x", description="d",
            )
            for i in range(n)
        ]

    class SourceA:
        name = "sourceA"

        def fetch(self):
            return make_jobs("sourceA", 3)

    class SourceB:
        name = "sourceB"

        def fetch(self):
            return make_jobs("sourceB", 2)

    monkeypatch.setattr(pipeline_mod, "build_enabled_connectors", lambda sources: [SourceA(), SourceB()])

    class FastLLM(LLMClient):
        def complete_json(self, system: str, user: str) -> str:
            return '{"score": 85, "dealbreaker_hit": false, "fails_minimum_requirements": false, "rationale": "ok"}'

    monkeypatch.setattr(app_mod, "build_llm_client", lambda cfg: FastLLM())

    app = create_app(settings)
    client = TestClient(app)

    r = client.post("/search", follow_redirects=False)
    assert r.status_code == 303

    final = None
    for _ in range(50):
        time.sleep(0.05)
        s = client.get("/search/status").json()
        if not s["search_running"]:
            final = s
            break

    assert final is not None, "search did not finish in time"
    assert final["considered_done"] == 5
    assert final["considered_total"] == 5
    assert final["matched_count"] == 5
