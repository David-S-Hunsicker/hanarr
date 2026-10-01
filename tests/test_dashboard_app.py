import datetime as dt
import sys
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

import keyring
import keyring.errors
import pytest

import hanarr.dashboard.app as app_mod
import hanarr.pipeline as pipeline_mod
from hanarr.config import Preferences, Settings
from hanarr.connectors.base import RawJobPosting
from hanarr.dashboard.app import create_app, format_posting_age, is_recent_posting, task_is_stuck
from hanarr.db import get_or_create_profile, make_session_factory
from hanarr.llm.base import LLMClient
from hanarr.models import ApplicationStatus, JobPosting, Profile, Reminder, ReminderType, ResumeVersion, SeenPosting
from hanarr.ollama_setup import HardwareInfo, OllamaDiagnostics, ModelRecommendation
from hanarr.search_state import new_search_state
from hanarr.update_state import new_update_state, reset_for_update


def test_task_is_stuck_false_when_not_running():
    assert task_is_stuck({"running": False, "started_at": None}, 1800.0) is False
    assert task_is_stuck({"running": False, "started_at": 100.0}, 1800.0, now=99999.0) is False


def test_jobs_page_disables_search_when_ollama_model_is_not_downloaded(tmp_path, monkeypatch):
    """"Block application functions until the model is completely
    downloaded" -- scoped to search: the button must be disabled and a
    clear, linked explanation shown before the user even clicks."""
    settings = _make_isolated_settings(tmp_path)
    settings.llm.provider = "ollama"
    diagnostics = OllamaDiagnostics(
        executable_path=r"C:\Ollama\ollama.exe", executable_version="test",
        service_reachable=True, service_error=None, installed_models=(),
        configured_model=settings.llm.model, configured_model_available=False,
        hardware=HardwareInfo(8, 20, "Windows"),
        recommendation=ModelRecommendation(settings.llm.model, "test", "low"),
    )
    monkeypatch.setattr(app_mod, "detect_ollama", lambda *a: diagnostics)

    html = TestClient(create_app(settings)).get("/").text
    assert 'id="search-btn" disabled' in html
    assert "isn't downloaded yet" in html
    assert '/config?tab=app#provider' in html


def test_jobs_page_has_a_sound_toggle_and_plays_a_tone_on_new_matches(tmp_path):
    """A user asked for an audible cue when a job is scored and added
    during a live search, with an easy way to turn it off -- no audio
    file is shipped, it's a synthesized Web Audio tone gated on both the
    toggle and an actual increase in matched_count (never on a run reset
    back to 0)."""
    settings = _make_isolated_settings(tmp_path)
    html = TestClient(create_app(settings)).get("/").text

    assert 'id="sound-toggle"' in html
    assert "function playMatchDing" in html
    assert "hanarr_sound_enabled" in html
    assert "if (data.matched_count > lastRenderedMatchedCount) playMatchDing();" in html


def test_jobs_page_search_enabled_when_ollama_model_is_downloaded(tmp_path, monkeypatch):
    settings = _make_isolated_settings(tmp_path)
    settings.llm.provider = "ollama"
    diagnostics = OllamaDiagnostics(
        executable_path=r"C:\Ollama\ollama.exe", executable_version="test",
        service_reachable=True, service_error=None, installed_models=(),
        configured_model=settings.llm.model, configured_model_available=True,
        hardware=HardwareInfo(8, 20, "Windows"),
        recommendation=ModelRecommendation(settings.llm.model, "test", "low"),
    )
    monkeypatch.setattr(app_mod, "detect_ollama", lambda *a: diagnostics)

    html = TestClient(create_app(settings)).get("/").text
    assert 'id="search-btn" disabled' not in html
    assert "isn't downloaded yet" not in html


def test_jobs_page_prompts_to_install_ollama_when_it_is_not_installed(tmp_path, monkeypatch):
    """The user reported expecting a prompt to install Ollama when it's
    missing entirely -- the generic "model isn't downloaded" message
    doesn't distinguish "no Ollama at all" from "Ollama's running but the
    model isn't pulled", so someone without Ollama never gets pointed at
    the (already-existing, consent-gated) install flow."""
    settings = _make_isolated_settings(tmp_path)
    settings.llm.provider = "ollama"
    diagnostics = OllamaDiagnostics(
        executable_path=None, executable_version=None,
        service_reachable=False, service_error="offline", installed_models=(),
        configured_model=settings.llm.model, configured_model_available=False,
        hardware=HardwareInfo(8, 20, "Windows"),
        recommendation=ModelRecommendation(settings.llm.model, "test", "low"),
    )
    monkeypatch.setattr(app_mod, "detect_ollama", lambda *a: diagnostics)

    html = TestClient(create_app(settings)).get("/").text
    assert 'id="search-btn" disabled' in html
    assert "Ollama isn't installed" in html
    assert '/config?tab=app#provider' in html


def test_jobs_page_shows_a_distinct_message_when_ollama_is_installed_but_not_running(tmp_path, monkeypatch):
    settings = _make_isolated_settings(tmp_path)
    settings.llm.provider = "ollama"
    diagnostics = OllamaDiagnostics(
        executable_path=r"C:\Ollama\ollama.exe", executable_version="test",
        service_reachable=False, service_error="offline", installed_models=(),
        configured_model=settings.llm.model, configured_model_available=False,
        hardware=HardwareInfo(8, 20, "Windows"),
        recommendation=ModelRecommendation(settings.llm.model, "test", "low"),
    )
    monkeypatch.setattr(app_mod, "detect_ollama", lambda *a: diagnostics)

    html = TestClient(create_app(settings)).get("/").text
    assert 'id="search-btn" disabled' in html
    assert "doesn't seem to be running" in html
    assert "Ollama isn't installed" not in html


def test_jobs_page_search_enabled_when_provider_is_none(tmp_path):
    """No local model to be "not ready" for when llm.provider isn't
    Ollama -- must never falsely block search."""
    settings = _make_isolated_settings(tmp_path)  # provider = "none" by default
    html = TestClient(create_app(settings)).get("/").text
    assert 'id="search-btn" disabled' not in html
    assert "isn't downloaded yet" not in html


def test_dashboard_uses_hanarr_product_name(tmp_path):
    settings = _make_isolated_settings(tmp_path)
    app = create_app(settings)

    assert app.title == "Hanarr"
    assert "Hanarr" in TestClient(app).get("/").text


def test_app_tab_prompts_to_install_ollama_near_the_resume_upload(tmp_path, monkeypatch):
    """Reported scenario: a user uploaded their resume before installing
    Ollama, silently getting only raw-text matching instead of structured
    extraction. Nudge them at the point of upload, not just after the
    fact on the Resume page."""
    settings = _make_isolated_settings(tmp_path)
    settings.llm.provider = "ollama"
    diagnostics = OllamaDiagnostics(
        executable_path=None, executable_version=None,
        service_reachable=False, service_error="offline", installed_models=(),
        configured_model=settings.llm.model, configured_model_available=False,
        hardware=HardwareInfo(8, 20, "Windows"),
        recommendation=ModelRecommendation(settings.llm.model, "test", "low"),
    )
    monkeypatch.setattr(app_mod, "detect_ollama", lambda *a: diagnostics)

    html = TestClient(create_app(settings)).get("/config", params={"tab": "app"}).text

    assert "Ollama isn't installed yet" in html
    assert "install it below" in html


def test_app_tab_does_not_prompt_to_install_ollama_when_already_installed(tmp_path, monkeypatch):
    settings = _make_isolated_settings(tmp_path)
    settings.llm.provider = "ollama"
    diagnostics = OllamaDiagnostics(
        executable_path=r"C:\Ollama\ollama.exe", executable_version="test",
        service_reachable=True, service_error=None, installed_models=(),
        configured_model=settings.llm.model, configured_model_available=True,
        hardware=HardwareInfo(8, 20, "Windows"),
        recommendation=ModelRecommendation(settings.llm.model, "test", "low"),
    )
    monkeypatch.setattr(app_mod, "detect_ollama", lambda *a: diagnostics)

    html = TestClient(create_app(settings)).get("/config", params={"tab": "app"}).text

    assert "Ollama isn't installed yet" not in html


def test_app_tab_does_not_prompt_to_install_ollama_when_provider_is_not_ollama(tmp_path):
    settings = _make_isolated_settings(tmp_path)  # provider = "none"
    html = TestClient(create_app(settings)).get("/config", params={"tab": "app"}).text
    assert "Ollama isn't installed yet" not in html


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


def _reachable_diagnostics(settings, **overrides):
    defaults = dict(
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
    defaults.update(overrides)
    return OllamaDiagnostics(**defaults)


def test_model_pull_runs_in_background_and_reports_live_progress(tmp_path, monkeypatch):
    """The old flow blocked the whole request until the entire download
    finished with zero visible progress. A pull must now run off-request
    and report live phase/percent through a poll endpoint."""
    settings = _make_isolated_settings(tmp_path)
    monkeypatch.setattr(app_mod, "detect_ollama", lambda *a: _reachable_diagnostics(settings))

    release_event = threading.Event()

    def fake_pull_model(model, base_url, *, consent, cancel_event=None, on_progress=None):
        on_progress({"status": "pulling manifest"})
        on_progress({"status": "downloading", "completed": 50, "total": 200})
        release_event.wait(timeout=2)
        on_progress({"status": "downloading", "completed": 200, "total": 200})
        return []

    monkeypatch.setattr(app_mod, "pull_model", fake_pull_model)

    client = TestClient(create_app(settings))
    started = client.post("/config/provider/pull", data={"model": "qwen2.5:7b"})
    assert started.status_code == 200

    for _ in range(50):
        status = client.get("/config/provider/pull/status").json()
        if status["percent"] == 25.0:
            break
        time.sleep(0.02)
    assert status["running"] is True
    assert status["percent"] == 25.0

    release_event.set()
    final = None
    for _ in range(50):
        time.sleep(0.02)
        final = client.get("/config/provider/pull/status").json()
        if not final["running"]:
            break
    assert final is not None and not final["running"]
    assert final["phase"] == "success"
    assert final["percent"] == 100.0


def test_model_pull_refuses_when_ollama_unreachable(tmp_path, monkeypatch):
    settings = _make_isolated_settings(tmp_path)
    monkeypatch.setattr(
        app_mod, "detect_ollama",
        lambda *a: _reachable_diagnostics(settings, service_reachable=False),
    )
    response = TestClient(create_app(settings)).post(
        "/config/provider/pull", data={"model": "qwen2.5:7b"}
    )
    assert response.status_code == 409


def test_model_pull_refuses_a_second_concurrent_pull(tmp_path, monkeypatch):
    settings = _make_isolated_settings(tmp_path)
    monkeypatch.setattr(app_mod, "detect_ollama", lambda *a: _reachable_diagnostics(settings))

    release_event = threading.Event()

    def fake_pull_model(model, base_url, *, consent, cancel_event=None, on_progress=None):
        release_event.wait(timeout=2)
        return []

    monkeypatch.setattr(app_mod, "pull_model", fake_pull_model)
    client = TestClient(create_app(settings))

    first = client.post("/config/provider/pull", data={"model": "qwen2.5:7b"})
    assert first.status_code == 200
    second = client.post("/config/provider/pull", data={"model": "qwen2.5:14b"})
    assert second.status_code == 409

    release_event.set()


def test_cancel_model_pull_sets_the_cancel_event(tmp_path, monkeypatch):
    settings = _make_isolated_settings(tmp_path)
    monkeypatch.setattr(app_mod, "detect_ollama", lambda *a: _reachable_diagnostics(settings))

    saw_cancel = threading.Event()

    def fake_pull_model(model, base_url, *, consent, cancel_event=None, on_progress=None):
        for _ in range(100):
            if cancel_event is not None and cancel_event.is_set():
                saw_cancel.set()
                return []
            time.sleep(0.02)
        return []

    monkeypatch.setattr(app_mod, "pull_model", fake_pull_model)
    client = TestClient(create_app(settings))
    client.post("/config/provider/pull", data={"model": "qwen2.5:7b"})

    cancelled = client.post("/config/provider/pull/cancel")
    assert cancelled.status_code == 200
    assert saw_cancel.wait(timeout=2)


def test_update_install_requires_explicit_approval(tmp_path):
    settings = _make_isolated_settings(tmp_path)
    client = TestClient(create_app(settings))
    response = client.post("/config/update/install", data={})
    assert response.status_code == 409
    assert response.json()["status"] == "approval_required"


def test_update_install_requires_consent(tmp_path):
    settings = _make_isolated_settings(tmp_path)
    client = TestClient(create_app(settings))
    response = client.post("/config/update/install", data={})
    assert response.status_code == 409
    assert response.json()["status"] == "approval_required"


def test_update_install_refuses_outside_a_packaged_build(tmp_path):
    """A source checkout (what every test runs as) has no installed .exe
    to replace -- must refuse cleanly rather than attempt anything."""
    settings = _make_isolated_settings(tmp_path)
    client = TestClient(create_app(settings))
    response = client.post("/config/update/install", data={"consent": "true"})
    assert response.status_code == 400
    assert "packaged" in response.json()["message"].lower() or "source" in response.json()["message"].lower()


def test_update_install_refuses_while_a_search_is_running(tmp_path, monkeypatch):
    settings = _make_isolated_settings(tmp_path)
    search_state = new_search_state()
    search_state["search_running"] = True
    client = TestClient(create_app(settings, search_state=search_state))

    response = client.post("/config/update/install", data={"consent": "true"})

    assert response.status_code == 409
    assert "search is running" in response.json()["message"]


def test_update_install_reports_up_to_date_when_no_newer_release(tmp_path, monkeypatch):
    settings = _make_isolated_settings(tmp_path)
    monkeypatch.setattr(app_mod, "check_for_update", lambda settings: {"status": "up_to_date", "current_version": "9.9.9"})
    monkeypatch.setattr(app_mod.self_update, "is_packaged_build", lambda: True)
    client = TestClient(create_app(settings))

    response = client.post("/config/update/install", data={"consent": "true"})

    assert response.status_code == 200
    assert response.json()["status"] == "up_to_date"


def test_update_install_downloads_verifies_and_applies_when_available(tmp_path, monkeypatch):
    settings = _make_isolated_settings(tmp_path)
    release_dict = {
        "version": "9.9.9",
        "release_notes_url": "https://example.test/notes",
        "notes": "",
        "assets": [{"name": "Hanarr-Setup-9.9.9.exe", "url": "https://example.test/installer", "sha256": "a" * 64, "platform": ""}],
    }
    monkeypatch.setattr(app_mod, "check_for_update", lambda settings: {"status": "update_available", "current_version": "0.1.0", "release": release_dict})
    monkeypatch.setattr(app_mod.self_update, "is_packaged_build", lambda: True)
    staged = tmp_path / "Hanarr-Setup-9.9.9.exe"
    monkeypatch.setattr(app_mod.self_update, "download_and_verify_installer", lambda asset, dest, **k: staged)
    applied = []
    monkeypatch.setattr(app_mod.self_update, "apply_update", lambda path: applied.append(path))
    client = TestClient(create_app(settings))

    response = client.post("/config/update/install", data={"consent": "true"})

    assert response.status_code == 200
    assert response.json() == {"status": "applying", "version": "9.9.9"}
    assert applied == [staged]


def test_update_status_route_reports_the_shared_state(tmp_path):
    settings = _make_isolated_settings(tmp_path)
    update_state = new_update_state()
    reset_for_update(update_state, version="9.9.9", notes_url="https://example.test/notes", installer_path="/tmp/x.exe", apply_at=123.0)
    client = TestClient(create_app(settings, update_state=update_state))

    response = client.get("/update/status")

    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["version"] == "9.9.9"
    assert body["apply_at"] == 123.0
    assert "installer_path" not in body  # local filesystem path -- never exposed to the page


def test_update_cancel_route_stops_the_countdown_but_keeps_the_staged_installer(tmp_path):
    settings = _make_isolated_settings(tmp_path)
    update_state = new_update_state()
    reset_for_update(update_state, version="9.9.9", notes_url="x", installer_path="/tmp/x.exe", apply_at=123.0)
    client = TestClient(create_app(settings, update_state=update_state))

    response = client.post("/update/cancel")

    assert response.status_code == 200
    assert response.json()["cancelled"] is True
    assert update_state["available"] is True  # still staged -- "Install now" must still work
    assert update_state["installer_path"] == "/tmp/x.exe"


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


def _default_profile_preferences(settings) -> Preferences:
    """Match criteria now live on the profile, not settings.preferences --
    see config.effective_preferences(). This reads the default profile's
    own saved copy, the way the app itself does after a Preferences tab
    save has forked it."""
    with make_session_factory(settings)() as session:
        profile = get_or_create_profile(session, settings)
        return Preferences.model_validate_json(profile.preferences_json)


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


def test_profile_rename_updates_the_name_and_is_reflected_everywhere(tmp_path):
    """A profile created for a one-off purpose (e.g. a QA/screenshot pass)
    can end up with a name like "UI Check" that then shows up in every
    page's header forever, with no way to fix it short of editing the
    database directly. Rename must be a real, reachable action."""
    settings = _make_isolated_settings(tmp_path)
    client = TestClient(create_app(settings))
    client.get("/")
    with make_session_factory(settings)() as session:
        profile_id = get_or_create_profile(session, settings).id

    renamed = client.post(
        f"/profiles/{profile_id}/rename", data={"name": "UI Check"}, follow_redirects=False
    )
    assert renamed.status_code == 303

    with make_session_factory(settings)() as session:
        assert session.get(Profile, profile_id).name == "UI Check"

    page = client.get("/").text
    assert "UI Check" in page

    client.post(f"/profiles/{profile_id}/rename", data={"name": "David"})
    with make_session_factory(settings)() as session:
        assert session.get(Profile, profile_id).name == "David"
    assert "UI Check" not in client.get("/").text


def test_profile_rename_ignores_a_blank_name(tmp_path):
    settings = _make_isolated_settings(tmp_path)
    client = TestClient(create_app(settings))
    client.get("/")
    with make_session_factory(settings)() as session:
        profile_id = get_or_create_profile(session, settings).id
        original_name = session.get(Profile, profile_id).name

    client.post(f"/profiles/{profile_id}/rename", data={"name": "   "})

    with make_session_factory(settings)() as session:
        assert session.get(Profile, profile_id).name == original_name


def test_profile_rename_404s_for_a_missing_profile(tmp_path):
    settings = _make_isolated_settings(tmp_path)
    client = TestClient(create_app(settings))

    response = client.post("/profiles/999999/rename", data={"name": "Anyone"})
    assert response.status_code == 404


def test_new_profile_starts_from_bare_preferences_defaults_not_the_shared_config(tmp_path):
    """A new profile is likely a different person, not a continuation of
    whoever set up this instance -- it must not inherit the existing
    (possibly highly specific) shared config.yaml preferences."""
    settings = _make_isolated_settings(tmp_path)
    settings.preferences.target_titles = ["Existing Person's Very Specific Title"]
    settings.preferences.locations = ["Existing Person's City"]
    client = TestClient(create_app(settings))

    created = client.post("/profiles", data={"name": "A different person"}, follow_redirects=False)
    new_id = int(created.cookies["hanarr_profile_id"])

    with make_session_factory(settings)() as session:
        new_profile = session.get(Profile, new_id)
        prefs = Preferences.model_validate_json(new_profile.preferences_json)
        assert prefs == Preferences()  # bare defaults, not a copy of the existing config
        assert prefs.target_titles == []
        assert prefs.locations == []


def test_two_profiles_preferences_tab_saves_are_fully_isolated(tmp_path, monkeypatch):
    """The core per-profile-preferences promise: saving Preferences while
    acting as profile A must never affect what profile B sees as its own
    values, and switching the active-profile cookie must render each
    profile's own saved values back correctly."""
    monkeypatch.chdir(tmp_path)
    settings = _make_isolated_settings(tmp_path)
    client = TestClient(create_app(settings))

    client.get("/")  # creates the default profile (profile A)
    with make_session_factory(settings)() as session:
        profile_a_id = get_or_create_profile(session, settings).id

    created = client.post("/profiles", data={"name": "Profile B"}, follow_redirects=False)
    profile_b_id = int(created.cookies["hanarr_profile_id"])

    # Save distinct preferences as profile A.
    client.cookies.set("hanarr_profile_id", str(profile_a_id))
    a_save = client.post(
        "/config/preferences",
        data={"target_titles": "Backend Engineer", "locations": "Austin, TX", "config_version": "0"},
        headers={"X-Autosave": "1"},
    )
    assert a_save.status_code == 200

    # Save different preferences as profile B.
    client.cookies.set("hanarr_profile_id", str(profile_b_id))
    b_save = client.post(
        "/config/preferences",
        data={"target_titles": "Data Scientist", "locations": "Remote", "config_version": "0"},
        headers={"X-Autosave": "1"},
    )
    assert b_save.status_code == 200

    with make_session_factory(settings)() as session:
        prefs_a = Preferences.model_validate_json(session.get(Profile, profile_a_id).preferences_json)
        prefs_b = Preferences.model_validate_json(session.get(Profile, profile_b_id).preferences_json)
    assert prefs_a.target_titles == ["Backend Engineer"]
    assert prefs_a.locations == ["Austin, TX"]
    assert prefs_b.target_titles == ["Data Scientist"]
    assert prefs_b.locations == ["Remote"]

    # The Preferences tab renders each profile's own saved values back,
    # not the other profile's or the shared config.yaml default.
    client.cookies.set("hanarr_profile_id", str(profile_a_id))
    page_a = client.get("/config?tab=preferences").text
    assert "Backend Engineer" in page_a
    assert "Data Scientist" not in page_a

    client.cookies.set("hanarr_profile_id", str(profile_b_id))
    page_b = client.get("/config?tab=preferences").text
    assert "Data Scientist" in page_b
    assert "Backend Engineer" not in page_b


def test_one_profiles_preferences_save_does_not_block_a_different_profiles_save(tmp_path, monkeypatch):
    """Regression test: the Preferences tab's stale-save protection
    originally compared against the single global config_version, shared
    by every tab and every profile. Two people using two profiles would
    then spuriously block each other -- profile A saving would bump the
    global counter, and profile B's already-open page (still holding the
    old counter value, since its own data was never touched) would get
    rejected as "changed elsewhere" even though nothing about B actually
    conflicted. Preferences now checks a per-profile counter instead."""
    monkeypatch.chdir(tmp_path)
    settings = _make_isolated_settings(tmp_path)
    client = TestClient(create_app(settings))

    client.get("/")
    with make_session_factory(settings)() as session:
        profile_a_id = get_or_create_profile(session, settings).id
    created = client.post("/profiles", data={"name": "Profile B"}, follow_redirects=False)
    profile_b_id = int(created.cookies["hanarr_profile_id"])

    # Both profiles "load" the Preferences tab at version 0.
    client.cookies.set("hanarr_profile_id", str(profile_a_id))
    assert client.get("/config?tab=preferences").text.count('name="config_version" value="0"') >= 1
    client.cookies.set("hanarr_profile_id", str(profile_b_id))
    assert client.get("/config?tab=preferences").text.count('name="config_version" value="0"') >= 1

    # Profile A saves -- advances the global config_version, but not B's
    # own preferences_version (B's data hasn't changed).
    client.cookies.set("hanarr_profile_id", str(profile_a_id))
    a_save = client.post(
        "/config/preferences",
        data={"target_titles": "Backend Engineer", "config_version": "0"},
        headers={"X-Autosave": "1"},
    )
    assert a_save.status_code == 200

    # Profile B's still-open tab, unaware of A's unrelated save, submits
    # its own save still claiming version 0 -- must succeed, since B's own
    # preferences genuinely haven't changed since B's page loaded.
    client.cookies.set("hanarr_profile_id", str(profile_b_id))
    b_save = client.post(
        "/config/preferences",
        data={"target_titles": "Data Scientist", "config_version": "0"},
        headers={"X-Autosave": "1"},
    )
    assert b_save.status_code == 200


def test_config_get_shows_the_shared_default_for_an_unforked_profile(tmp_path):
    """A profile that has never saved its own Preferences tab must keep
    transparently showing the shared config.yaml value -- the live
    fallback that makes this change a no-op for an existing single-profile
    install until a second profile actually diverges."""
    settings = _make_isolated_settings(tmp_path)
    settings.preferences.target_titles = ["Shared Config Title"]
    client = TestClient(create_app(settings))

    page = client.get("/config?tab=preferences").text

    assert "Shared Config Title" in page


def test_config_page_shows_the_app_version(tmp_path):
    """The version was previously only visible in pyproject.toml/__init__.py
    -- surfaced on Settings so the user can tell what they're running
    without digging into the source tree."""
    from hanarr import __version__

    settings = _make_isolated_settings(tmp_path)
    client = TestClient(create_app(settings))

    page = client.get("/config?tab=preferences").text

    assert f"v{__version__}" in page


def test_unhandled_exception_returns_500_and_logs_a_traceback_instead_of_crashing_silently(
    tmp_path, monkeypatch
):
    """Regression coverage for a real gap: the packaged desktop build runs
    --windowed (no console -- see scripts/build_windows.ps1), so an
    unhandled exception previously vanished with nothing for a user to
    report beyond "Internal Server Error". The global exception handler in
    create_app() must turn that into a real logged traceback (which
    logging_setup.configure_file_logging persists to a file at runtime)
    without the test client itself raising."""
    settings = _make_isolated_settings(tmp_path)
    client = TestClient(create_app(settings), raise_server_exceptions=False)

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated failure for the regression test")

    monkeypatch.setattr(app_mod, "get_active_profile", _boom)

    logged = []
    monkeypatch.setattr(app_mod.logger, "exception", lambda msg, *a: logged.append(msg % a))

    response = client.get("/")

    assert response.status_code == 500
    assert "Internal Server Error" in response.text
    assert logged and "Unhandled error on GET /" in logged[0]


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


def test_retry_resume_extraction_route_reruns_without_reuploading(tmp_path, monkeypatch):
    """The scenario reported: a resume uploaded before Ollama was ready
    fails extraction with no obvious way to redo it short of re-uploading
    the same file. The retry route must succeed using only the profile's
    already-stored text, once a working LLM is available."""
    monkeypatch.chdir(tmp_path)  # the upload saves a relative config.yaml -- must never touch the real repo's
    settings = _make_isolated_settings(tmp_path)  # provider = "none" -> upload's extraction fails
    client = TestClient(create_app(settings))

    upload = client.post("/config/resume", files={"file": ("resume.txt", b"Jane Doe. AI Engineer.", "text/plain")})
    assert upload.status_code == 200
    for _ in range(50):
        status = client.get("/config/resume/status").json()
        if not status["running"]:
            break
        time.sleep(0.05)
    assert status["error"]  # NullLLMClient raises -> extraction failed on upload

    def fake_retry_resume_extraction(session, settings, llm, *, profile_id=None):
        from hanarr.db import get_active_profile
        profile = get_active_profile(session, settings, profile_id)
        profile.resume_summary_json = '{"titles": ["AI Engineer"], "skills": ["pytorch"]}'
        session.commit()
        return profile, {"titles": ["AI Engineer"], "skills": ["pytorch"]}, False

    monkeypatch.setattr(app_mod, "retry_resume_extraction", fake_retry_resume_extraction)

    retry = client.post("/config/resume/reparse")
    assert retry.status_code == 200
    for _ in range(50):
        status = client.get("/config/resume/status").json()
        if not status["running"]:
            break
        time.sleep(0.05)
    assert status["error"] is None
    assert "AI Engineer" in status["result"]


def test_retry_resume_extraction_route_requires_a_resume_to_already_exist(tmp_path):
    settings = _make_isolated_settings(tmp_path)
    client = TestClient(create_app(settings))

    response = client.post("/config/resume/reparse")

    assert response.status_code == 400
    assert "No resume has been uploaded" in response.json()["error"]


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


def test_add_manual_job_creates_and_scores_a_posting(tmp_path):
    settings = _make_isolated_settings(tmp_path)
    session_factory = make_session_factory(settings)
    with session_factory() as session:
        profile = get_or_create_profile(session, settings)
        profile.resume_text = "Experienced Python backend engineer."
        session.commit()

    client = TestClient(create_app(settings))
    response = client.post(
        "/jobs/manual",
        data={
            "company": "Acme", "title": "Backend Engineer",
            "url": "https://example.test/job/1", "location": "Remote",
            "remote": "on", "salary_min": "120000", "salary_max": "150000",
            "description": "Python and FastAPI experience required.",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303

    with session_factory() as session:
        job = session.query(JobPosting).filter_by(source="manual").one()
        assert job.company == "Acme"
        assert job.title == "Backend Engineer"
        assert job.remote is True
        assert job.salary_min == 120000
        assert job.salary_max == 150000
        assert job.fit_score is not None  # scored via the same keyword fallback a real search uses

    html = client.get("/").text
    assert "Acme" in html


def test_add_manual_job_rejects_missing_required_fields(tmp_path):
    settings = _make_isolated_settings(tmp_path)
    client = TestClient(create_app(settings))

    response = client.post("/jobs/manual", data={"company": "   ", "title": "Engineer", "url": "https://x.test"})

    assert response.status_code == 400
    with make_session_factory(settings)() as session:
        assert session.query(JobPosting).count() == 0


def test_add_manual_job_ignores_an_invalid_salary_instead_of_erroring(tmp_path):
    settings = _make_isolated_settings(tmp_path)
    client = TestClient(create_app(settings))

    response = client.post(
        "/jobs/manual",
        data={"company": "Acme", "title": "Engineer", "url": "https://x.test", "salary_min": "not a number"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    with make_session_factory(settings)() as session:
        job = session.query(JobPosting).filter_by(source="manual").one()
        assert job.salary_min is None


def test_reminder_ics_export_downloads_a_calendar_file(tmp_path):
    settings = _make_isolated_settings(tmp_path)
    session_factory = make_session_factory(settings)
    with session_factory() as session:
        profile = get_or_create_profile(session, settings)
        job = JobPosting(
            profile_id=profile.id, source="test", external_id="1", company="Acme",
            title="Backend Engineer", url="https://example.test/1",
        )
        session.add(job)
        session.commit()
        reminder = Reminder(
            profile_id=profile.id, job_id=job.id, type=ReminderType.INTERVIEW_PREP,
            message="Prep for the on-site.",
            due_at=dt.datetime(2026, 3, 5, 14, 0, 0),
        )
        session.add(reminder)
        session.commit()
        reminder_id = reminder.id

    client = TestClient(create_app(settings))
    response = client.get(f"/reminders/{reminder_id}.ics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/calendar")
    assert f'filename="hanarr-reminder-{reminder_id}.ics"' in response.headers["content-disposition"]
    assert "BEGIN:VEVENT" in response.text
    assert "Acme" in response.text


def test_reminder_ics_export_404s_for_a_missing_reminder(tmp_path):
    settings = _make_isolated_settings(tmp_path)
    client = TestClient(create_app(settings))

    response = client.get("/reminders/999999.ics")

    assert response.status_code == 404


def test_all_reminders_ics_export_includes_only_pending_reminders_for_the_active_profile(tmp_path):
    settings = _make_isolated_settings(tmp_path)
    session_factory = make_session_factory(settings)
    with session_factory() as session:
        profile = get_or_create_profile(session, settings)
        session.add_all([
            Reminder(
                profile_id=profile.id, type=ReminderType.FOLLOW_UP, message="pending one",
                due_at=dt.datetime(2026, 3, 5, 14, 0, 0),
            ),
            Reminder(
                profile_id=profile.id, type=ReminderType.FOLLOW_UP, message="already done",
                due_at=dt.datetime(2026, 3, 1, 9, 0, 0), completed=True,
            ),
        ])
        session.commit()

    client = TestClient(create_app(settings))
    response = client.get("/reminders.ics")

    assert response.status_code == 200
    assert response.text.count("BEGIN:VEVENT") == 1
    assert "pending one" in response.text
    assert "already done" not in response.text


def test_applications_page_links_to_calendar_export(tmp_path):
    settings = _make_isolated_settings(tmp_path)
    session_factory = make_session_factory(settings)
    with session_factory() as session:
        profile = get_or_create_profile(session, settings)
        job = JobPosting(
            profile_id=profile.id, source="test", external_id="1", company="Acme",
            title="Backend Engineer", url="https://example.test/1", status=ApplicationStatus.APPLIED,
        )
        session.add(job)
        session.commit()
        session.add(Reminder(
            profile_id=profile.id, job_id=job.id, type=ReminderType.FOLLOW_UP,
            message="Follow up.", due_at=dt.datetime(2026, 3, 5, 14, 0, 0),
        ))
        session.commit()
        reminder_id = session.query(Reminder).filter_by(profile_id=profile.id).one().id

    client = TestClient(create_app(settings))
    html = client.get("/applications").text

    assert 'href="/reminders.ics"' in html
    assert f'href="/reminders/{reminder_id}.ics"' in html


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


def test_jobs_panel_matches_index_page_content(tmp_path):
    """"When a new job is scored the numbers and page should automatically
    update" -- the dashboard polls /jobs/panel while a search runs and
    swaps its stats_html/jobs_html into the page instead of waiting for a
    full reload. Its content must exactly match what the full index page
    would render for the same filter, or the two would visibly drift."""
    settings = _make_isolated_settings(tmp_path)
    session_factory = make_session_factory(settings)

    with session_factory() as session:
        profile = get_or_create_profile(session, settings)
        session.add(SeenPosting(profile_id=profile.id, source="test", external_id="0"))
        session.add(
            JobPosting(
                profile_id=profile.id, source="test", external_id="0", company="Acme",
                title="Backend Engineer", url="u", fit_score=80, status=ApplicationStatus.NEW,
            )
        )
        session.commit()

    client = TestClient(create_app(settings))

    panel = client.get("/jobs/panel").json()
    assert "Backend Engineer" in panel["jobs_html"]
    assert "1" in panel["stats_html"] and "considered" in panel["stats_html"]

    index_html = client.get("/").text
    assert "Backend Engineer" in index_html
    # The partial's fragments must be a subset of what index.html rendered
    # for the same data, proving the two share the same underlying context
    # rather than two independently-maintained copies that could drift.
    assert panel["jobs_html"].strip() in index_html


def test_jobs_panel_respects_status_filter(tmp_path):
    settings = _make_isolated_settings(tmp_path)
    session_factory = make_session_factory(settings)

    with session_factory() as session:
        profile = get_or_create_profile(session, settings)
        session.add(
            JobPosting(
                profile_id=profile.id, source="test", external_id="0", company="Acme",
                title="New Job", url="u", fit_score=80, status=ApplicationStatus.NEW,
            )
        )
        session.add(
            JobPosting(
                profile_id=profile.id, source="test", external_id="1", company="Acme",
                title="Applied Job", url="u", fit_score=80, status=ApplicationStatus.APPLIED,
            )
        )
        session.commit()

    client = TestClient(create_app(settings))
    panel = client.get("/jobs/panel", params={"status": "applied"}).json()

    assert "Applied Job" in panel["jobs_html"]
    assert "New Job" not in panel["jobs_html"]


def test_jobs_page_recent_filter_shows_only_postings_within_the_window(tmp_path):
    """"We need a way to filter ... for jobs that have been posted
    recently, say within a week." """
    settings = _make_isolated_settings(tmp_path)
    with make_session_factory(settings)() as session:
        profile = get_or_create_profile(session, settings)
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        session.add(JobPosting(
            profile_id=profile.id, source="test", external_id="0", company="Acme",
            title="Fresh Job", url="u", fit_score=80, posted_at=now - dt.timedelta(days=2),
        ))
        session.add(JobPosting(
            profile_id=profile.id, source="test", external_id="1", company="Acme",
            title="Stale Job", url="u", fit_score=80, posted_at=now - dt.timedelta(days=30),
        ))
        session.add(JobPosting(
            profile_id=profile.id, source="test", external_id="2", company="Acme",
            title="Undated Job", url="u", fit_score=80, posted_at=None,
        ))
        session.commit()

    client = TestClient(create_app(settings))

    unfiltered = client.get("/").text
    assert "Fresh Job" in unfiltered and "Stale Job" in unfiltered and "Undated Job" in unfiltered

    recent = client.get("/", params={"recent": "1"}).text
    assert "Fresh Job" in recent
    assert "Stale Job" not in recent
    assert "Undated Job" not in recent
    # The toggle itself must be marked active, and the count in its own
    # label must reflect only what actually matches the window.
    assert 'class="active">Posted within 7d (1)' in recent


def test_jobs_page_sort_newest_first_orders_by_posted_at(tmp_path):
    settings = _make_isolated_settings(tmp_path)
    with make_session_factory(settings)() as session:
        profile = get_or_create_profile(session, settings)
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        # Deliberately give the older posting the higher fit score, so a
        # newest-first result only makes sense if sort actually overrides
        # the default fit-score ordering.
        session.add(JobPosting(
            profile_id=profile.id, source="test", external_id="0", company="Acme",
            title="Older Higher-Fit Job", url="u", fit_score=95, posted_at=now - dt.timedelta(days=5),
        ))
        session.add(JobPosting(
            profile_id=profile.id, source="test", external_id="1", company="Acme",
            title="Newer Lower-Fit Job", url="u", fit_score=40, posted_at=now - dt.timedelta(days=1),
        ))
        session.commit()

    client = TestClient(create_app(settings))
    html = client.get("/", params={"sort": "recent"}).text

    newer_index = html.index("Newer Lower-Fit Job")
    older_index = html.index("Older Higher-Fit Job")
    assert newer_index < older_index, "newest-first sort should list the more recent posting first"


def test_jobs_panel_filter_links_preserve_the_other_active_dimensions(tmp_path):
    """Switching status shouldn't silently drop an active recency filter
    or sort choice, and vice versa -- each pill link must carry the other
    two dimensions forward."""
    settings = _make_isolated_settings(tmp_path)
    client = TestClient(create_app(settings))

    html = client.get("/", params={"status": "applied", "sort": "recent", "recent": "1"}).text
    assert "status=applied&amp;sort=recent&amp;recent=1" in html or "status=applied&sort=recent&recent=1" in html


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

    monkeypatch.setattr(pipeline_mod, "build_enabled_connectors", lambda sources, **kwargs: [SlowConnector()])
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

    monkeypatch.setattr(pipeline_mod, "build_enabled_connectors", lambda sources, **kwargs: [SourceA(), SourceB()])

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
    monkeypatch.setattr(pipeline_mod, "build_enabled_connectors", lambda sources, **kwargs: [])

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


def test_guide_page_renders_with_nav_and_explains_the_workflow(tmp_path):
    settings = _make_isolated_settings(tmp_path)
    client = TestClient(create_app(settings))

    response = client.get("/guide")
    assert response.status_code == 200
    html = response.text
    assert "Recommended order of operations" in html
    assert "Improve my fit" in html
    assert 'href="/guide">Guide</a>' in html


def test_every_main_page_links_to_the_guide_page(tmp_path):
    """The Guide nav link was added to every page's own duplicated nav
    block (no shared include exists) -- regression-test that none of them
    were missed."""
    settings = _make_isolated_settings(tmp_path)
    client = TestClient(create_app(settings))

    for page in ("/", "/config", "/coaching", "/resume", "/skills", "/applications", "/profiles"):
        html = client.get(page).text
        assert 'href="/guide"' in html, f"{page} is missing a link to /guide"


def test_all_pages_share_identical_shell_layout_values(tmp_path):
    """"All page formatting should basically look the same and not shift
    the text around... the header line shouldn't shift or move." Each page
    template keeps its own <style> block, so nothing prevents them from
    drifting -- this pins the shared shell values (body width, header
    spacing, nav spacing, heading size) so a future edit to one page can't
    silently diverge from the rest without a test failing."""
    settings = _make_isolated_settings(tmp_path)
    client = TestClient(create_app(settings))

    pages = ["/", "/config", "/coaching", "/resume", "/skills", "/applications", "/profiles", "/guide"]
    responses = {page: client.get(page) for page in pages}
    for page, response in responses.items():
        assert response.status_code == 200, f"{page} did not load"
    bodies = {page: response.text for page, response in responses.items()}

    import re

    def first(pattern, text):
        match = re.search(pattern, text)
        assert match, f"pattern {pattern!r} not found"
        return match.group(1)

    max_widths = {page: first(r"max-width:\s*(\d+)px;\s*margin:\s*0 auto", html) for page, html in bodies.items()}
    assert len(set(max_widths.values())) == 1, f"body max-width differs across pages: {max_widths}"
    assert list(max_widths.values())[0] == "1000"

    header_margins = {
        page: first(r"\.(?:page-)?header\s*\{[^}]*margin-bottom:\s*([\d.]+rem)", html)
        for page, html in bodies.items()
    }
    assert len(set(header_margins.values())) == 1, f"header margin-bottom differs across pages: {header_margins}"

    nav_margins = {
        page: first(r"\.(?:nav|tabs)\s*\{[^}]*margin:\s*([\d.a-z ]+;)", html)
        for page, html in bodies.items()
    }
    assert len(set(nav_margins.values())) == 1, f"nav margin differs across pages: {nav_margins}"

    h1_sizes = {page: first(r"h1[^{]*\{[^}]*font-size:\s*([\d.]+rem)", html) for page, html in bodies.items()}
    assert len(set(h1_sizes.values())) == 1, f"h1 font-size differs across pages: {h1_sizes}"


def test_onboarding_banner_shows_incomplete_items_on_jobs_and_settings(tmp_path):
    """A brand-new profile (no resume, no target titles) must see a clear
    "get set up" checklist on both the Jobs page and Settings, not the old
    empty-state text that referenced a `hanarr search` CLI command a
    packaged-app user doesn't have."""
    settings = _make_isolated_settings(tmp_path)
    client = TestClient(create_app(settings))

    for page in ("/", "/config"):
        html = client.get(page).text
        assert "Get set up" in html
        assert "Add your resume" in html
        assert "Set your target titles" in html
        assert "set up a local AI model" in html
        assert "hanarr search" not in html

    empty_state = client.get("/").text
    assert "Add your resume and search preferences above to get started." in empty_state


def test_onboarding_banner_hides_once_resume_and_titles_are_set(tmp_path):
    settings = _make_isolated_settings(tmp_path)
    settings.preferences.target_titles = ["Software Engineer"]
    with make_session_factory(settings)() as session:
        profile = get_or_create_profile(session, settings)
        profile.resume_text = "Jane Doe. Senior Engineer."
        session.commit()

    client = TestClient(create_app(settings))
    for page in ("/", "/config"):
        html = client.get(page).text
        assert "Get set up" not in html

    empty_state = client.get("/").text
    assert 'No jobs yet — click "Run search now" above.' in empty_state
    assert "hanarr search" not in empty_state


def test_onboarding_banner_shows_partial_progress(tmp_path):
    """Resume uploaded but no target titles yet -- the resume item should
    show as done, titles as not, and the banner should still be visible
    since all_done requires both."""
    settings = _make_isolated_settings(tmp_path)
    with make_session_factory(settings)() as session:
        profile = get_or_create_profile(session, settings)
        profile.resume_text = "Jane Doe. Senior Engineer."
        session.commit()

    client = TestClient(create_app(settings))
    html = client.get("/").text
    assert "Get set up" in html
    assert '<li class="done"><span class="check">✓</span> <a href="/config?tab=app#resume">Add your resume</a></li>' in html
    assert '<li class=""><span class="check">○</span> <a href="/config?tab=preferences">Set your target titles</a></li>' in html


def test_debug_filtered_page_is_empty_before_any_search(tmp_path):
    settings = _make_isolated_settings(tmp_path)
    html = TestClient(create_app(settings)).get("/debug/filtered").text
    assert "Nothing to show yet" in html


def test_debug_filtered_page_shows_rejected_postings_after_a_search(tmp_path, monkeypatch):
    """"Why filtered out" debug view: a posting rejected by the prefilter
    during a real search must show up here with the specific reason, not
    just silently vanish."""
    settings = _make_isolated_settings(tmp_path)
    settings.preferences.keywords_exclude = ["unpaid"]

    class _FakeConnector:
        name = "test"

        def fetch(self):
            return [
                RawJobPosting(
                    source="test", external_id="1", company="Acme",
                    title="Unpaid Intern", location="Remote", remote=True,
                    url="https://example.test/1", description="This is an unpaid role.",
                )
            ]

    monkeypatch.setattr(pipeline_mod, "build_enabled_connectors", lambda sources, **kwargs: [_FakeConnector()])

    client = TestClient(create_app(settings))
    client.post("/search", follow_redirects=False)
    for _ in range(50):
        time.sleep(0.05)
        if not client.get("/search/status").json()["search_running"]:
            break

    html = client.get("/debug/filtered").text
    assert "Unpaid Intern" in html
    assert "Acme" in html
    assert "unpaid" in html


def test_why_this_score_label_is_not_duplicated(tmp_path):
    """Regression test: the "Why this score" disclosure's visible label is
    injected once by CSS (:before on the <summary>, which also supplies
    the arrow icon) -- the <summary> tag must not ALSO contain that text
    as real content, or it renders twice ("Why this score ▸  Why this
    score")."""
    settings = _make_isolated_settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        session.add(JobPosting(
            profile_id=profile.id, source="test", external_id="rationale-1", company="Acme",
            title="Engineer", url="https://example.test/rationale-1", fit_score=80.0,
            fit_rationale="You have strong relevant experience.",
        ))
        session.commit()

    html = TestClient(create_app(settings)).get("/").text
    assert "<summary></summary>" in html
    assert "<summary>Why this score</summary>" not in html


def test_search_status_reports_already_seen_count_for_resumed_postings(tmp_path, monkeypatch):
    """"Do we think it's possible to resume a search that was paused or
    disrupted?" -- yes, via the SeenPosting dedup, but it needs to be
    visible. A posting already scored in a prior run must be counted and
    reported separately from genuinely new postings on the next run."""
    settings = _make_isolated_settings(tmp_path)
    with make_session_factory(settings)() as session:
        profile = get_or_create_profile(session, settings)
        # Simulates a posting already scored by an earlier, interrupted run.
        session.add(SeenPosting(profile_id=profile.id, source="test", external_id="already-scored"))
        session.commit()

    class _FakeConnector:
        name = "test"

        def fetch(self):
            return [
                RawJobPosting(
                    source="test", external_id="already-scored", company="Acme",
                    title="Seen Before", location="Remote", remote=True,
                    url="https://example.test/1", description="d",
                ),
                RawJobPosting(
                    source="test", external_id="brand-new", company="Acme",
                    title="Never Seen", location="Remote", remote=True,
                    url="https://example.test/2", description="d",
                ),
            ]

    monkeypatch.setattr(pipeline_mod, "build_enabled_connectors", lambda sources, **kwargs: [_FakeConnector()])

    client = TestClient(create_app(settings))
    client.post("/search", follow_redirects=False)
    final = None
    for _ in range(50):
        time.sleep(0.05)
        s = client.get("/search/status").json()
        if not s["search_running"]:
            final = s
            break

    assert final is not None, "search did not finish in time"
    assert final["already_seen_count"] == 1


def test_search_status_shows_a_scheduled_run_via_the_shared_search_state(tmp_path):
    """"A user won't know that Ollama is consuming resources unless the
    application is showing that." Before search_state was shared between
    the scheduler and the dashboard, a scheduled search had no callback at
    all and updated nothing the dashboard could see. Passing the same
    dict both places (as cli.py's `serve` command does) must make a
    scheduled run visible through the exact same /search/status route a
    manual run uses."""
    settings = _make_isolated_settings(tmp_path)
    search_state = new_search_state()
    app = create_app(settings, search_state=search_state)
    client = TestClient(app)

    # Simulates the scheduler's background thread updating the same
    # shared object, entirely independent of any request the dashboard
    # itself has handled.
    from hanarr.search_state import on_progress, reset_for_run
    reset_for_run(search_state, 1, "scheduled")
    on_progress(search_state, {"event": "scoring", "title": "Engineer", "company": "Acme"})

    status = client.get("/search/status").json()
    assert status["search_running"] is True
    assert status["trigger"] == "scheduled"
    assert status["scoring_count"] == 1


def test_search_activity_badge_present_on_every_non_jobs_page(tmp_path):
    """A scheduled search can start while the user is on any page, not
    just Jobs -- the site-wide badge (polling /search/status) must be
    present everywhere else so background LLM activity is never silently
    invisible just because of which page happens to be open."""
    settings = _make_isolated_settings(tmp_path)
    client = TestClient(create_app(settings))

    for page in ["/config", "/coaching", "/resume", "/skills", "/applications", "/profiles", "/debug/filtered"]:
        html = client.get(page).text
        assert 'id="search-activity-badge"' in html, f"{page} is missing the search activity badge"


def test_update_available_banner_present_on_every_page(tmp_path):
    """A found update can be staged while the user is on any page -- the
    banner (polling /update/status) must be present everywhere, including
    Jobs, which doesn't include the search-activity badge partial."""
    settings = _make_isolated_settings(tmp_path)
    client = TestClient(create_app(settings))

    for page in ["/", "/config", "/coaching", "/resume", "/skills", "/applications", "/profiles", "/debug/filtered"]:
        html = client.get(page).text
        assert 'id="update-available-banner"' in html, f"{page} is missing the update-available banner"


def test_updates_tab_shows_auto_update_controls(tmp_path):
    settings = _make_isolated_settings(tmp_path)
    settings.updates.auto_update = True
    settings.updates.check_interval_hours = 12
    html = TestClient(create_app(settings)).get("/config", params={"tab": "updates"}).text

    assert 'id="updates_auto_update" name="updates_auto_update" checked' in html
    assert 'id="updates_check_interval_hours" name="updates_check_interval_hours" value="12"' in html


def test_preferences_tab_shows_common_dealbreakers_as_checked_checkboxes(tmp_path):
    """Common dealbreakers should be checkboxes, not something the user has
    to type out as free text every time."""
    from hanarr.matching import COMMON_DEALBREAKERS

    settings = _make_isolated_settings(tmp_path)
    settings.preferences.dealbreakers = [COMMON_DEALBREAKERS[0][1], "A fully custom one"]
    html = TestClient(create_app(settings)).get("/config", params={"tab": "preferences"}).text

    checked_input = f'name="dealbreaker_{COMMON_DEALBREAKERS[0][0]}" checked'
    unchecked_input = f'name="dealbreaker_{COMMON_DEALBREAKERS[1][0]}" checked'
    assert checked_input in html
    assert unchecked_input not in html
    assert COMMON_DEALBREAKERS[0][1] in html  # the checkbox's own label text
    assert "A fully custom one" in html  # preserved in the custom textarea
    assert 'id="dealbreakers_custom"' in html


def test_schedule_tab_displays_hours_or_days_based_on_the_stored_interval(tmp_path):
    """The stored config only ever has hours -- the Settings UI shows it
    as whichever unit divides evenly, so "every 3 days" doesn't force the
    user to do hours*24 math themselves."""
    settings = _make_isolated_settings(tmp_path)
    settings.schedule.search_interval_hours = 6
    client = TestClient(create_app(settings))
    html = client.get("/config", params={"tab": "schedule"}).text
    assert 'id="search_interval_value" min="1" value="6"' in html
    assert '<option value="hours" selected>Hours</option>' in html

    settings.schedule.search_interval_hours = 72
    html = client.get("/config", params={"tab": "schedule"}).text
    assert 'id="search_interval_value" min="1" value="3"' in html
    assert '<option value="days" selected>Days</option>' in html


def test_schedule_form_rejects_an_interval_below_the_safety_minimum(tmp_path):
    settings = _make_isolated_settings(tmp_path)
    client = TestClient(create_app(settings))

    response = client.post(
        "/config/schedule",
        data={"search_interval_hours": "-5", "reminder_check_interval_hours": "1", "follow_up_after_days": "7"},
    )
    assert response.status_code == 200
    assert "Couldn" in response.text  # the shared error banner's "Couldn't save" heading
    assert "search_interval_hours" in response.text


def test_preferences_autosave_saves_and_returns_json_without_redirecting(tmp_path, monkeypatch):
    """Preferences fields autosave on blur/tab-navigation (see config.html)
    by POSTing the same form with an X-Autosave header -- that request
    wants a small JSON ack back, not the normal full-page redirect, since
    the page never actually navigates for an autosave."""
    monkeypatch.chdir(tmp_path)  # the save writes a relative config.yaml -- must never touch the real repo's
    settings = _make_isolated_settings(tmp_path)
    client = TestClient(create_app(settings))

    response = client.post(
        "/config/preferences",
        data={"target_titles": "Staff Engineer", "min_fit_score": "70"},
        headers={"X-Autosave": "1"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert response.json() == {"saved": True, "config_version": 1}
    assert _default_profile_preferences(settings).target_titles == ["Staff Engineer"]
    assert settings.matching.min_fit_score == 70


def test_config_post_rejects_a_stale_config_version(tmp_path, monkeypatch):
    """Regression test for a real data-loss incident: locations, salary
    floor, and every job-source board were silently wiped back to blank
    when a stale browser tab's full-form autosave overwrote fresher
    settings. A form claiming a config_version that no longer matches the
    live value must be refused outright -- not partially applied, not
    silently accepted -- since its other, untouched fields reflect a
    settings state that no longer exists."""
    monkeypatch.chdir(tmp_path)
    settings = _make_isolated_settings(tmp_path)
    client = TestClient(create_app(settings))

    # A legitimate save "elsewhere" (e.g. a different tab, or the resume
    # upload flow's auto-populated preferences) -- a real browser submits
    # every current field, so this includes locations/company_boards too,
    # moving the live version from 0 to 1.
    elsewhere = client.post(
        "/config/preferences",
        data={
            "remote_ok": "on", "min_fit_score": "60",
            "locations": "Austin, TX",
            "greenhouse_enabled": "on", "greenhouse_company_boards": "stripe\nairbnb",
            "config_version": "0",
        },
        headers={"X-Autosave": "1"},
    )
    assert elsewhere.status_code == 200

    # The stale tab: still believes it's version 0, and its cached DOM has
    # blank locations/company_boards (loaded before "elsewhere" ran).
    response = client.post(
        "/config/preferences",
        data={
            "remote_ok": "on", "min_fit_score": "60",
            "locations": "",  # the stale tab's blank snapshot of a field that was actually populated
            "config_version": "0",  # still claims the original, now-outdated version
        },
        headers={"X-Autosave": "1"},
    )

    assert response.status_code == 409
    assert response.json()["saved"] is False
    # The critical assertion: the stale write must never have been applied.
    assert _default_profile_preferences(settings).locations == ["Austin, TX"]
    assert settings.sources.greenhouse.company_boards == ["stripe", "airbnb"]


def test_config_post_accepts_a_matching_config_version_and_advances_it(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = _make_isolated_settings(tmp_path)
    client = TestClient(create_app(settings))

    first = client.post(
        "/config/preferences",
        data={"remote_ok": "on", "min_fit_score": "60", "config_version": "0"},
        headers={"X-Autosave": "1"},
    )
    assert first.status_code == 200
    assert first.json()["config_version"] == 1

    second = client.post(
        "/config/preferences",
        data={"remote_ok": "on", "min_fit_score": "60", "locations": "Denver, CO", "config_version": "1"},
        headers={"X-Autosave": "1"},
    )
    assert second.status_code == 200
    assert second.json()["config_version"] == 2
    assert _default_profile_preferences(settings).locations == ["Denver, CO"]


def test_config_post_without_a_version_field_is_not_blocked(tmp_path, monkeypatch):
    """Backward compatible: a submit that doesn't include config_version at
    all (e.g. an older cached page, or a non-browser client) isn't
    rejected outright -- the protection only activates once a client
    actually asserts a version that turns out to be wrong."""
    monkeypatch.chdir(tmp_path)
    settings = _make_isolated_settings(tmp_path)
    client = TestClient(create_app(settings))

    response = client.post(
        "/config/preferences",
        data={"remote_ok": "on", "min_fit_score": "60"},  # no config_version at all
        headers={"X-Autosave": "1"},
    )

    assert response.status_code == 200
    assert response.json()["saved"] is True


def test_config_page_renders_the_current_config_version(tmp_path):
    settings = _make_isolated_settings(tmp_path)
    html = TestClient(create_app(settings)).get("/config", params={"tab": "preferences"}).text
    assert 'name="config_version" value="0"' in html


def test_autosave_returns_json_errors_instead_of_a_rendered_page(tmp_path):
    """Same _handle_config_post code path, exercised via the schedule tab's
    existing known validation failure -- proves the autosave error branch
    returns JSON rather than the HTML error banner."""
    settings = _make_isolated_settings(tmp_path)
    client = TestClient(create_app(settings))

    response = client.post(
        "/config/schedule",
        data={"search_interval_hours": "-5", "reminder_check_interval_hours": "1", "follow_up_after_days": "7"},
        headers={"X-Autosave": "1"},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["saved"] is False
    assert any("search_interval_hours" in e for e in body["errors"])


def test_schedule_form_saves_daily_mode_and_time_of_day(tmp_path, monkeypatch):
    # save_settings_to_yaml writes to a relative "config.yaml" -- chdir into
    # tmp_path so a successful save never touches the real repo checkout's
    # config.yaml.
    monkeypatch.chdir(tmp_path)
    settings = _make_isolated_settings(tmp_path)
    client = TestClient(create_app(settings))

    response = client.post(
        "/config/schedule",
        data={
            "search_interval_hours": "6",
            "reminder_check_interval_hours": "1",
            "follow_up_after_days": "7",
            "search_schedule_mode": "daily",
            "search_time_of_day": "09:15",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert settings.schedule.search_schedule_mode == "daily"
    assert settings.schedule.search_time_of_day == "09:15"


def test_schedule_form_rejects_an_invalid_time_of_day(tmp_path):
    settings = _make_isolated_settings(tmp_path)
    client = TestClient(create_app(settings))

    response = client.post(
        "/config/schedule",
        data={
            "search_interval_hours": "6",
            "reminder_check_interval_hours": "1",
            "follow_up_after_days": "7",
            "search_schedule_mode": "daily",
            "search_time_of_day": "not-a-time",
        },
    )
    assert response.status_code == 200
    assert "Couldn" in response.text
    assert "search_time_of_day" in response.text


def test_schedule_tab_shows_local_timezone_next_to_the_daily_time_field(tmp_path):
    settings = _make_isolated_settings(tmp_path)
    client = TestClient(create_app(settings))
    html = client.get("/config", params={"tab": "schedule"}).text
    assert 'id="search_time_of_day"' in html
    import tzlocal
    assert str(tzlocal.get_localzone()) in html


class _FakeKeyringBackend(keyring.backend.KeyringBackend):
    """In-memory stand-in for the OS credential store, so these tests never
    touch the real Windows Credential Manager / macOS Keychain."""
    priority = 1

    def __init__(self):
        self._store: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self._store.get((service, username))

    def set_password(self, service, username, password):
        self._store[(service, username)] = password

    def delete_password(self, service, username):
        if (service, username) not in self._store:
            raise keyring.errors.PasswordDeleteError("not found")
        del self._store[(service, username)]


@pytest.fixture
def fake_keyring(monkeypatch):
    backend = _FakeKeyringBackend()
    monkeypatch.setattr(keyring, "get_password", backend.get_password)
    monkeypatch.setattr(keyring, "set_password", backend.set_password)
    monkeypatch.setattr(keyring, "delete_password", backend.delete_password)
    return backend


def test_saving_an_anthropic_key_stores_it_in_keyring_not_config_yaml(tmp_path, monkeypatch, fake_keyring):
    monkeypatch.chdir(tmp_path)  # a successful save must never touch the real repo's config.yaml
    settings = _make_isolated_settings(tmp_path)
    client = TestClient(create_app(settings))

    response = client.post(
        "/config/app",
        data={
            "resume_path": settings.profile.resume_path,
            "llm_provider": "anthropic",
            "llm_model": "claude-haiku-4-5",
            "llm_base_url": settings.llm.base_url,
            "llm_timeout_seconds": "60",
            "dashboard_host": settings.dashboard.host,
            "dashboard_port": str(settings.dashboard.port),
            "anthropic_api_key": "sk-ant-super-secret",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert settings.llm.api_key == "sk-ant-super-secret"
    assert fake_keyring._store[("hanarr", "anthropic_api_key")] == "sk-ant-super-secret"

    config_yaml = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert "sk-ant-super-secret" not in config_yaml
    assert "api_key" not in config_yaml


def test_app_config_page_shows_per_task_model_sizing_and_saves_an_override(tmp_path, monkeypatch, fake_keyring):
    monkeypatch.chdir(tmp_path)
    settings = _make_isolated_settings(tmp_path)
    client = TestClient(create_app(settings))

    page = client.get("/config?tab=app").text
    assert "Per-task model sizing" in page
    assert 'name="agent_model_market_analysis"' in page
    assert "runs on every posting in every search" in page

    response = client.post(
        "/config/app",
        data={
            "resume_path": settings.profile.resume_path,
            "llm_provider": settings.llm.provider,
            "llm_model": settings.llm.model,
            "llm_base_url": settings.llm.base_url,
            "llm_timeout_seconds": "60",
            "agent_model_profiler": "",
            "agent_model_market_analysis": "qwen2.5:14b",
            "agent_model_curriculum": "",
            "agent_model_evaluator": "",
            "agent_model_resume_writer": "",
            "dashboard_host": settings.dashboard.host,
            "dashboard_port": str(settings.dashboard.port),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert settings.agents.market_analysis.model == "qwen2.5:14b"
    assert settings.agents.profiler.model is None

    saved_page = client.get("/config?tab=app").text
    assert 'value="qwen2.5:14b"' in saved_page


def test_blank_agent_model_override_clears_a_previously_saved_one(tmp_path, monkeypatch, fake_keyring):
    monkeypatch.chdir(tmp_path)
    settings = _make_isolated_settings(tmp_path)
    settings.agents.market_analysis.model = "qwen2.5:14b"
    client = TestClient(create_app(settings))

    client.post(
        "/config/app",
        data={
            "resume_path": settings.profile.resume_path,
            "llm_provider": settings.llm.provider,
            "llm_model": settings.llm.model,
            "llm_base_url": settings.llm.base_url,
            "llm_timeout_seconds": "60",
            "agent_model_profiler": "",
            "agent_model_market_analysis": "",
            "agent_model_curriculum": "",
            "agent_model_evaluator": "",
            "agent_model_resume_writer": "",
            "dashboard_host": settings.dashboard.host,
            "dashboard_port": str(settings.dashboard.port),
        },
        follow_redirects=False,
    )
    assert settings.agents.market_analysis.model is None


def test_blank_anthropic_key_field_leaves_the_stored_key_unchanged(tmp_path, monkeypatch, fake_keyring):
    monkeypatch.chdir(tmp_path)  # a successful save must never touch the real repo's config.yaml
    settings = _make_isolated_settings(tmp_path)
    fake_keyring.set_password("hanarr", "anthropic_api_key", "already-stored")
    settings.llm.api_key = "already-stored"
    client = TestClient(create_app(settings))

    client.post(
        "/config/app",
        data={
            "resume_path": settings.profile.resume_path,
            "llm_provider": "anthropic",
            "llm_model": settings.llm.model,
            "llm_base_url": settings.llm.base_url,
            "llm_timeout_seconds": "60",
            "dashboard_host": settings.dashboard.host,
            "dashboard_port": str(settings.dashboard.port),
            "anthropic_api_key": "",
        },
        follow_redirects=False,
    )

    assert settings.llm.api_key == "already-stored"
    assert fake_keyring._store[("hanarr", "anthropic_api_key")] == "already-stored"


def test_clearing_the_anthropic_key_checkbox_removes_it_from_keyring(tmp_path, monkeypatch, fake_keyring):
    monkeypatch.chdir(tmp_path)  # a successful save must never touch the real repo's config.yaml
    settings = _make_isolated_settings(tmp_path)
    fake_keyring.set_password("hanarr", "anthropic_api_key", "already-stored")
    settings.llm.api_key = "already-stored"
    client = TestClient(create_app(settings))

    client.post(
        "/config/app",
        data={
            "resume_path": settings.profile.resume_path,
            "llm_provider": "anthropic",
            "llm_model": settings.llm.model,
            "llm_base_url": settings.llm.base_url,
            "llm_timeout_seconds": "60",
            "dashboard_host": settings.dashboard.host,
            "dashboard_port": str(settings.dashboard.port),
            "anthropic_api_key": "",
            "anthropic_api_key_clear": "on",
        },
        follow_redirects=False,
    )

    assert settings.llm.api_key is None
    assert ("hanarr", "anthropic_api_key") not in fake_keyring._store


def test_config_page_never_renders_the_actual_stored_key(tmp_path, fake_keyring):
    settings = _make_isolated_settings(tmp_path)
    settings.llm.api_key = "sk-ant-should-not-leak"
    client = TestClient(create_app(settings))

    html = client.get("/config", params={"tab": "app"}).text

    assert "sk-ant-should-not-leak" not in html
    assert "already set" in html


def test_saving_smtp_password_stores_it_in_keyring_not_config_yaml(tmp_path, monkeypatch, fake_keyring):
    monkeypatch.chdir(tmp_path)  # a successful save must never touch the real repo's config.yaml
    settings = _make_isolated_settings(tmp_path)
    client = TestClient(create_app(settings))

    response = client.post(
        "/config/schedule",
        data={
            "search_interval_hours": "6",
            "reminder_check_interval_hours": "1",
            "follow_up_after_days": "7",
            "smtp_password": "hunter2",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert settings.reminders.email.smtp_password == "hunter2"
    assert fake_keyring._store[("hanarr", "smtp_password")] == "hunter2"

    config_yaml = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert "hunter2" not in config_yaml
