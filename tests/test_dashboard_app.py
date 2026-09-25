import datetime as dt
import sys
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

import hanarr.dashboard.app as app_mod
import hanarr.pipeline as pipeline_mod
from hanarr.config import Settings
from hanarr.connectors.base import RawJobPosting
from hanarr.dashboard.app import create_app, format_posting_age, is_recent_posting, task_is_stuck
from hanarr.db import get_or_create_profile, make_session_factory
from hanarr.llm.base import LLMClient
from hanarr.models import ApplicationStatus, JobPosting, Profile, Reminder, ReminderType, ResumeVersion, SeenPosting
from hanarr.ollama_setup import HardwareInfo, OllamaDiagnostics, ModelRecommendation


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


def test_manual_search_surfaces_a_clean_error_when_llm_is_unavailable(tmp_path, monkeypatch):
    """A configured-but-unreachable LLM must show a clear, specific error
    on the dashboard -- not a generic "check server logs" guess, and not
    silently proceed to fall back to rule-based scoring on every posting."""
    settings = _make_isolated_settings(tmp_path)

    def _raise_unavailable(session, settings, profile, llm, **kwargs):
        raise pipeline_mod.LLMUnavailableError(
            "Ollama isn't reachable at http://127.0.0.1:11434 — start Ollama and try again."
        )

    monkeypatch.setattr(app_mod, "run_search_cycle", _raise_unavailable)

    app = create_app(settings)
    client = TestClient(app)

    client.post("/search", follow_redirects=False)
    final = None
    for _ in range(50):
        time.sleep(0.05)
        s = client.get("/search/status").json()
        if not s["search_running"]:
            final = s
            break

    assert final is not None, "search did not finish in time"
    assert "Ollama isn't reachable" in final["last_search_result"]


def test_profiles_page_lists_and_creates_and_switches(tmp_path):
    """"We do need to add profiles for multiple people" -- local profile
    slots, no auth. Creating one and switching sets a cookie the rest of
    the app reads to decide which profile's data to show."""
    settings = _make_isolated_settings(tmp_path)
    client = TestClient(create_app(settings))

    # Visiting any page creates the default profile.
    client.get("/")
    with make_session_factory(settings)() as session:
        default_id = get_or_create_profile(session, settings).id

    page = client.get("/profiles").text
    assert settings.profile.name in page

    created = client.post("/profiles", data={"name": "Jordan"}, follow_redirects=False)
    assert created.status_code == 303
    assert created.cookies.get("hanarr_profile_id") is not None
    new_id = int(created.cookies["hanarr_profile_id"])
    assert new_id != default_id

    with make_session_factory(settings)() as session:
        assert session.get(Profile, new_id).name == "Jordan"

    switched = client.post(f"/profiles/{default_id}/activate", follow_redirects=False)
    assert switched.cookies.get("hanarr_profile_id") == str(default_id)


def test_two_profiles_see_only_their_own_jobs(tmp_path, monkeypatch):
    """Regression test for the core multi-profile promise: switching the
    active-profile cookie must isolate jobs (and everything else keyed by
    profile_id) -- profile A's saved jobs must never leak into profile B's
    dashboard, and vice versa."""
    settings = _make_isolated_settings(tmp_path)
    with make_session_factory(settings)() as session:
        profile_a = get_or_create_profile(session, settings)
        profile_b = Profile(name="Jordan")
        session.add(profile_b)
        session.commit()
        session.add_all([
            JobPosting(
                profile_id=profile_a.id, source="test", external_id="a1", company="Acme",
                title="Profile A Job", url="https://example.test/a1", fit_score=80.0,
            ),
            JobPosting(
                profile_id=profile_b.id, source="test", external_id="b1", company="Acme",
                title="Profile B Job", url="https://example.test/b1", fit_score=80.0,
            ),
        ])
        session.commit()
        profile_a_id, profile_b_id = profile_a.id, profile_b.id

    client = TestClient(create_app(settings))
    client.cookies.set("hanarr_profile_id", str(profile_a_id))
    page_a = client.get("/").text
    assert "Profile A Job" in page_a
    assert "Profile B Job" not in page_a

    client.cookies.set("hanarr_profile_id", str(profile_b_id))
    page_b = client.get("/").text
    assert "Profile B Job" in page_b
    assert "Profile A Job" not in page_b


def test_resume_uploads_for_two_profiles_do_not_collide_on_disk(tmp_path, monkeypatch):
    """Regression test: before per-profile storage, every upload was saved
    to the same fixed resumes/resume.<ext> path regardless of who uploaded
    it -- a second profile's resume would silently overwrite the first's
    file on disk (though not its already-parsed DB text). Uploads must now
    land under a per-profile subdirectory, and only the default profile's
    upload should update the shared config.yaml resume_path."""
    settings = _make_isolated_settings(tmp_path)
    settings.profile.resume_path = str(tmp_path / "resume.md")
    monkeypatch.setattr(app_mod, "DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    client = TestClient(create_app(settings))

    with make_session_factory(settings)() as session:
        default_id = get_or_create_profile(session, settings).id

    created = client.post("/profiles", data={"name": "Jordan"}, follow_redirects=False)
    second_id = int(created.cookies["hanarr_profile_id"])

    def _upload_and_wait(profile_id: int, text: bytes, filename: str):
        client.cookies.set("hanarr_profile_id", str(profile_id))
        response = client.post("/config/resume", files={"file": (filename, text, "text/plain")})
        assert response.status_code == 200
        for _ in range(50):
            status = client.get("/config/resume/status").json()
            if not status["running"]:
                return status
            time.sleep(0.05)
        raise AssertionError("resume re-parse did not finish in time")

    _upload_and_wait(default_id, b"Default profile resume text.", "default.txt")
    _upload_and_wait(second_id, b"Jordan's resume text.", "jordan.txt")

    resumes_dir = (tmp_path / "resume.md").parent
    assert (resumes_dir / str(default_id) / "resume.txt").read_text() == "Default profile resume text."
    assert (resumes_dir / str(second_id) / "resume.txt").read_text() == "Jordan's resume text."

    with make_session_factory(settings)() as session:
        default_profile = session.get(Profile, default_id)
        second_profile = session.get(Profile, second_id)
        assert default_profile.resume_text == "Default profile resume text."
        assert second_profile.resume_text == "Jordan's resume text."

    # Only the default profile's upload is allowed to move the shared
    # config.yaml pointer -- Jordan's upload must not silently redirect it.
    assert Path(settings.profile.resume_path) == resumes_dir / str(default_id) / "resume.txt"


def test_resume_version_label_can_be_set_and_cleared(tmp_path):
    """"Multiple resumes per person as an option" -- version history needs
    a way to tell saved resumes apart, so labels must be settable and
    clearable (blank label reverts to showing "Version N")."""
    settings = _make_isolated_settings(tmp_path)
    with make_session_factory(settings)() as session:
        profile = get_or_create_profile(session, settings)
        version = ResumeVersion(profile_id=profile.id, content="Jane Doe", is_active=True)
        session.add(version)
        session.commit()
        version_id = version.id

    client = TestClient(create_app(settings))
    renamed = client.post(f"/api/resume/versions/{version_id}/label", json={"label": "Backend-focused"})
    assert renamed.status_code == 200
    assert renamed.json() == {"id": version_id, "label": "Backend-focused"}

    page = client.get("/resume").text
    assert "Backend-focused" in page

    cleared = client.post(f"/api/resume/versions/{version_id}/label", json={"label": "  "})
    assert cleared.json()["label"] is None


def test_resume_version_label_404s_for_another_profiles_version(tmp_path):
    settings = _make_isolated_settings(tmp_path)
    with make_session_factory(settings)() as session:
        get_or_create_profile(session, settings)

    client = TestClient(create_app(settings))
    response = client.post("/api/resume/versions/999/label", json={"label": "x"})
    assert response.status_code == 404


def test_resume_download_serves_active_version_as_text_attachment(tmp_path):
    """"Export on resume is a must have" -- only extracted plain text is
    retained (not the original PDF bytes), so the download is always a
    .txt file built from the active ResumeVersion, named after whatever
    the user originally uploaded."""
    settings = _make_isolated_settings(tmp_path)
    with make_session_factory(settings)() as session:
        profile = get_or_create_profile(session, settings)
        profile.resume_original_filename = "David_Resume_2026.pdf"
        session.add(ResumeVersion(profile_id=profile.id, content="Jane Doe\nSenior Engineer", is_active=True))
        session.commit()

    client = TestClient(create_app(settings))
    response = client.get("/resume/download")
    assert response.status_code == 200
    assert response.text == "Jane Doe\nSenior Engineer"
    assert response.headers["content-disposition"] == 'attachment; filename="David_Resume_2026.txt"'


def test_resume_download_404s_with_no_resume_on_file(tmp_path):
    settings = _make_isolated_settings(tmp_path)
    client = TestClient(create_app(settings))
    response = client.get("/resume/download")
    assert response.status_code == 404


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
                due_at=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
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


def test_search_failure_shows_actual_error_not_a_generic_guess(tmp_path, monkeypatch):
    """Regression test: the failure message used to be a generic "check
    server logs" with a guessed cause, which can point at entirely the
    wrong thing -- e.g. a reachable LLM that 404s because the configured
    model was never pulled, not a connectivity problem at all. The actual
    exception text must reach the user."""
    settings = _make_isolated_settings(tmp_path)

    def failing_search_cycle(*args, **kwargs):
        raise RuntimeError("simulated 404 Not Found from the model endpoint")

    monkeypatch.setattr(app_mod, "run_search_cycle", failing_search_cycle)

    app = create_app(settings)
    client = TestClient(app)

    client.post("/search", follow_redirects=False)
    for _ in range(50):
        status = client.get("/search/status").json()
        if not status["search_running"]:
            break
        time.sleep(0.05)

    assert "simulated 404 Not Found" in status["last_search_result"]
    assert "check server logs" not in status["last_search_result"]


def test_suggest_keywords_failure_shows_actual_error_not_a_generic_guess(tmp_path, monkeypatch):
    settings = _make_isolated_settings(tmp_path)

    class FailingLLM(LLMClient):
        def complete_json(self, system: str, user: str) -> str:
            raise RuntimeError("simulated 404 Not Found from the model endpoint")

    monkeypatch.setattr(app_mod, "build_llm_client", lambda cfg: FailingLLM())

    app = create_app(settings)
    client = TestClient(app)
    with make_session_factory(settings)() as session:
        profile = get_or_create_profile(session, settings)
        profile.resume_text = "Jane Doe. AI Engineer."
        session.commit()

    client.post("/config/suggest-keywords")
    for _ in range(50):
        status = client.get("/config/suggest-keywords/status").json()
        if not status["running"]:
            break
        time.sleep(0.05)

    assert "simulated 404 Not Found" in status["error"]
    assert "is the LLM reachable" not in status["error"]


def test_restart_route_spawns_a_real_process_instead_of_execv(tmp_path, monkeypatch):
    """Regression test for a real reported hang: os.execv does not provide
    true process replacement on Windows -- the C runtime emulates it by
    spawning a child that inherits this process's open handles, including
    the listening socket, then blocks this process until that child exits.
    The child's own bind attempt then collides with this process still
    holding the port, and neither side recovers: the server hangs,
    unresponsive to every request, exactly as reported. The restart route
    must spawn a genuinely independent process (subprocess.Popen does not
    inherit handles by default) and exit immediately instead."""
    settings = _make_isolated_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    popen_calls = []
    exit_calls = []
    monkeypatch.setattr(app_mod.subprocess, "Popen", lambda *a, **k: popen_calls.append((a, k)))
    monkeypatch.setattr(app_mod.os, "_exit", lambda code: exit_calls.append(code))

    response = client.post("/restart")

    assert response.status_code == 200
    assert response.json() == {"restarting": True}
    # The actual shutdown/spawn is deliberately deferred ~0.5s so this
    # response reaches the client before the process acts on it.
    assert popen_calls == [] and exit_calls == []

    for _ in range(30):
        if popen_calls and exit_calls:
            break
        time.sleep(0.05)

    args, kwargs = popen_calls[0]
    assert args[0] == [sys.argv[0], *sys.argv[1:]]
    assert kwargs.get("close_fds") is True
    assert exit_calls == [0]


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
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)

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
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)

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


def test_applications_page_excludes_new_jobs_and_groups_the_rest_by_status(tmp_path):
    """Regression test: the Applications page was an unbuilt placeholder
    ("reserved for the next transition milestone") even though the status
    data it needs already existed on JobPosting. It must show jobs the user
    has actually made a decision about (anything past "new"), grouped by
    status, and never show "new"/undecided postings -- those stay on Jobs."""
    settings = _make_isolated_settings(tmp_path)
    session_factory = make_session_factory(settings)

    with session_factory() as session:
        profile = get_or_create_profile(session, settings)
        session.add(JobPosting(
            profile_id=profile.id, source="test", external_id="new", company="Acme",
            title="Untouched Posting", url="u", fit_score=80, status=ApplicationStatus.NEW,
        ))
        session.add(JobPosting(
            profile_id=profile.id, source="test", external_id="applied", company="Beta",
            title="Applied Posting", url="u", fit_score=75, status=ApplicationStatus.APPLIED,
        ))
        session.add(JobPosting(
            profile_id=profile.id, source="test", external_id="offer", company="Gamma",
            title="Offer Posting", url="u", fit_score=90, status=ApplicationStatus.OFFER,
        ))
        session.commit()

    app = create_app(settings)
    client = TestClient(app)
    html = client.get("/applications").text

    assert "Untouched Posting" not in html
    assert "Applied Posting" in html
    assert "Offer Posting" in html
    # Interviewing/offer are grouped ahead of applied in the attention-order
    # this page uses, so Offer's own <section> heading should appear first.
    assert html.index("Offer") < html.index("Applied") < html.index("Applied Posting")


def test_applications_page_shows_pending_reminders_scoped_to_their_job(tmp_path):
    settings = _make_isolated_settings(tmp_path)
    session_factory = make_session_factory(settings)

    with session_factory() as session:
        profile = get_or_create_profile(session, settings)
        job = JobPosting(
            profile_id=profile.id, source="test", external_id="1", company="Acme",
            title="Applied Posting", url="u", fit_score=75, status=ApplicationStatus.APPLIED,
        )
        session.add(job)
        session.flush()
        session.add(Reminder(
            profile_id=profile.id, job_id=job.id, type=ReminderType.FOLLOW_UP,
            message="Check in with the recruiter", due_at=dt.datetime(2030, 1, 1), completed=False,
        ))
        session.add(Reminder(
            profile_id=profile.id, job_id=job.id, type=ReminderType.INTERVIEW_PREP,
            message="Already handled", due_at=dt.datetime(2020, 1, 1), completed=True,
        ))
        session.commit()

    app = create_app(settings)
    client = TestClient(app)
    html = client.get("/applications").text

    assert "Check in with the recruiter" in html
    assert "Already handled" not in html  # completed reminders are not shown


def test_applications_page_shows_a_helpful_empty_state(tmp_path):
    settings = _make_isolated_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    html = client.get("/applications").text

    assert "reserved for the next transition milestone" not in html
    assert "Jobs" in html


class _FakeSchedulerJob:
    def __init__(self, next_run_time):
        self.next_run_time = next_run_time


class _FakeScheduler:
    """Duck-typed stand-in for apscheduler.BackgroundScheduler -- create_app
    only ever calls .get_job(id), so a real scheduler (with its own thread)
    isn't needed to test what the dashboard shows."""

    def __init__(self, jobs: dict):
        self._jobs = jobs

    def get_job(self, job_id):
        return self._jobs.get(job_id)


def test_search_status_shows_next_scheduled_run_and_persisted_last_search(tmp_path):
    """Regression test: the dashboard previously had no visibility into the
    background scheduler at all -- no next-run time, and "last search"
    lived only in in-memory state that reset to blank on every restart."""
    settings = _make_isolated_settings(tmp_path)
    next_run = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=3)
    scheduler = _FakeScheduler({"search": _FakeSchedulerJob(next_run)})

    with make_session_factory(settings)() as session:
        profile = get_or_create_profile(session, settings)
        profile.last_search_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(hours=2)
        profile.last_search_new_count = 7
        profile.last_search_trigger = "scheduled"
        session.commit()

    app = create_app(settings, scheduler=scheduler)
    client = TestClient(app)

    status = client.get("/search/status").json()
    assert status["next_search_label"] == "in 3h"
    assert status["last_search_label"] == "Last scheduled search: 7 new posting(s), 2h ago"

    html = client.get("/").text
    assert "Next automatic search in 3h" in html
    assert "Last scheduled search: 7 new posting(s), 2h ago" in html


def test_search_status_omits_schedule_info_without_a_scheduler(tmp_path):
    """Most routes (including every test) run with scheduler=None -- must
    not error, just show nothing scheduled."""
    settings = _make_isolated_settings(tmp_path)
    app = create_app(settings)  # no scheduler
    client = TestClient(app)

    status = client.get("/search/status").json()
    assert status["next_search_label"] is None
    assert status["next_reminder_check_label"] is None
    assert status["last_search_label"] is None


def test_manual_search_persists_last_search_to_the_profile(tmp_path, monkeypatch):
    """A manual "Run search now" must also update the persisted fields, not
    just the in-memory state -- otherwise this info only ever reflects
    scheduled runs, not the button the user actually clicked."""
    settings = _make_isolated_settings(tmp_path)
    monkeypatch.setattr(pipeline_mod, "build_enabled_connectors", lambda sources: [])

    app = create_app(settings)
    client = TestClient(app)

    client.post("/search", follow_redirects=False)
    final = None
    for _ in range(50):
        time.sleep(0.05)
        s = client.get("/search/status").json()
        if not s["search_running"]:
            final = s
            break

    assert final is not None, "search did not finish in time"
    with make_session_factory(settings)() as session:
        profile = get_or_create_profile(session, settings)
        assert profile.last_search_trigger == "manual"
        assert profile.last_search_new_count == 0
        assert profile.last_search_at is not None
