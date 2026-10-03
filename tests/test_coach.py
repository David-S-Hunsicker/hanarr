import json

from fastapi.testclient import TestClient

import hanarr.dashboard.app as app_module
from hanarr.coach import CoachError, add_message, ask_coach, build_context, recent_messages
from hanarr.config import Settings
from hanarr.dashboard.app import create_app
from hanarr.db import get_or_create_profile, make_session_factory
from hanarr.models import ChatRole, JobPosting, Project, ProjectMode, ProjectStatus


class FakeLLM:
    def __init__(self, response):
        self.response = response
        self.last_user_prompt = None

    def complete_json(self, system, user):
        self.last_user_prompt = user
        return self.response


class FailingLLM:
    def complete_json(self, system, user):
        raise RuntimeError("provider unreachable")


def _settings(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    settings.llm.provider = "none"
    return settings


def test_build_context_includes_resume_jobs_and_active_projects(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        profile.resume_summary_json = json.dumps({"titles": ["Engineer"]})
        job = JobPosting(
            profile_id=profile.id, source="test", external_id="1", company="Acme",
            title="Backend Engineer", url="https://example.test/1",
            fit_score=85, fit_rationale="Strong Python match.",
        )
        session.add(job)
        session.flush()
        project = Project(
            profile_id=profile.id, title="Kubernetes practice project",
            mode=ProjectMode.REUSABLE_SKILL, status=ProjectStatus.ACTIVE,
            target_outcome="Deploy a small service to a real cluster.",
        )
        session.add(project)
        session.commit()

        context = build_context(session, profile)

    assert "Backend Engineer" in context
    assert "Strong Python match." in context
    assert "Kubernetes practice project" in context
    assert "Deploy a small service" in context
    assert "Hanarr" in context  # the app-knowledge summary is always included


def test_build_context_handles_a_profile_with_nothing_yet(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        context = build_context(session, profile)

    assert "none uploaded yet" in context
    assert "none scored yet" in context
    assert "ACTIVE COACHING PROJECTS: none." in context


def test_ask_coach_persists_user_message_and_answer(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        session.commit()

        llm = FakeLLM(json.dumps({"answer": "Focus on the Acme posting first."}))
        answer = ask_coach(session, profile, llm, "What should I prioritize?")
        session.commit()

        assert answer == "Focus on the Acme posting first."
        messages = recent_messages(session, profile.id)
        assert [m.role for m in messages] == [ChatRole.USER, ChatRole.ASSISTANT]
        assert messages[0].content == "What should I prioritize?"
        assert messages[1].content == "Focus on the Acme posting first."
        assert "CONTEXT" in llm.last_user_prompt


def test_ask_coach_raises_coach_error_on_llm_failure_without_fabricating_an_answer(tmp_path):
    """No deterministic fallback exists for open-ended chat -- a failure
    must not silently persist a made-up answer the way other LLM-backed
    features fall back to a safe deterministic result."""
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        session.commit()

        try:
            ask_coach(session, profile, FailingLLM(), "Hello?")
            assert False, "expected a CoachError"
        except CoachError:
            pass
        session.commit()

        messages = recent_messages(session, profile.id)
        # The user's own message is still kept -- they did send it -- but
        # no assistant answer follows a failed call.
        assert len(messages) == 1
        assert messages[0].role == ChatRole.USER


def test_ask_coach_raises_coach_error_on_malformed_json(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        session.commit()
        try:
            ask_coach(session, profile, FakeLLM("not json"), "Hello?")
            assert False, "expected a CoachError"
        except CoachError:
            pass


def test_coach_page_shows_not_ready_banner_under_provider_none(tmp_path):
    settings = _settings(tmp_path)
    client = TestClient(create_app(settings))
    page = client.get("/coach").text
    assert "needs an LLM provider configured" in page
    assert 'id="chat-form"' not in page


def test_coach_page_round_trips_a_message(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path / "data")
    settings.llm.provider = "anthropic"
    settings.llm.api_key = "fake"
    monkeypatch.setattr(app_module, "build_llm_client", lambda cfg: FakeLLM(json.dumps({"answer": "You are a strong match for Acme."})))
    client = TestClient(create_app(settings))

    empty_page = client.get("/coach").text
    assert 'id="chat-form"' in empty_page
    assert "Ask Coach anything" in empty_page

    response = client.post("/api/coach/messages", json={"message": "Who should I follow up with?"})
    assert response.status_code == 200
    assert response.json() == {"answer": "You are a strong match for Acme."}

    page = client.get("/coach").text
    assert "Who should I follow up with?" in page
    assert "You are a strong match for Acme." in page


def test_coach_message_requires_non_empty_text(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    settings.llm.provider = "anthropic"
    settings.llm.api_key = "fake"
    client = TestClient(create_app(settings))
    response = client.post("/api/coach/messages", json={"message": "   "})
    assert response.status_code == 400


def test_coach_message_returns_a_clean_error_on_llm_failure(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path / "data")
    settings.llm.provider = "anthropic"
    settings.llm.api_key = "fake"
    monkeypatch.setattr(app_module, "build_llm_client", lambda cfg: FailingLLM())
    client = TestClient(create_app(settings))

    response = client.post("/api/coach/messages", json={"message": "Hello?"})
    assert response.status_code == 502
    assert "error" in response.json()


def test_coach_nav_link_present_on_every_page(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    client = TestClient(create_app(settings))
    for page in ["/", "/config", "/coaching", "/resume", "/skills", "/applications", "/profiles", "/prep", "/guide", "/debug/filtered"]:
        html = client.get(page).text
        assert 'href="/coach"' in html, f"{page} is missing a link to /coach"
