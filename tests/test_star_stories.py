import json

from fastapi.testclient import TestClient

from hanarr.config import Settings
from hanarr.dashboard.app import create_app
import hanarr.dashboard.app as app_module
from hanarr.db import get_or_create_profile, make_session_factory
from hanarr.models import ApplicationStatus, JobPosting
from hanarr.star_stories import generate_star_questions, get_or_create_story, review_story, save_story_draft


class FakeLLM:
    def __init__(self, response):
        self.response = response

    def complete_json(self, system, user):
        return self.response


def _settings(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    settings.llm.provider = "none"
    return settings


def test_generate_star_questions_uses_llm_output_when_valid(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        profile.resume_summary_json = json.dumps({"titles": ["Engineer"], "seniority": "senior", "industries": ["fintech"]})
        session.commit()
        llm = FakeLLM(json.dumps({"questions": [
            {"question": "Tell me about a time you shipped under pressure.", "competency": "delivery"},
        ]}))
        questions = generate_star_questions(session, profile, llm)
        session.commit()
        assert len(questions) == 1
        assert questions[0].competency == "delivery"
        assert questions[0].source == "generic"


def test_generate_star_questions_falls_back_to_fixed_set_on_bad_llm_output(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        questions = generate_star_questions(session, profile, FakeLLM("not json"))
        session.commit()
        assert len(questions) == 6
        assert {q.competency for q in questions} == {
            "leadership", "conflict", "failure", "ambiguity", "technical tradeoff", "cross-team collaboration",
        }


def test_generate_star_questions_weights_toward_a_job_when_given(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        job = JobPosting(
            profile_id=profile.id, source="test", external_id="1", company="Acme",
            title="Staff Engineer", url="https://example.test/1", description="Own the platform roadmap.",
        )
        session.add(job)
        session.commit()
        job_id = job.id

        llm = FakeLLM(json.dumps({"questions": [
            {"question": "Tell me about a time you owned a platform roadmap.", "competency": "leadership"},
        ]}))
        questions = generate_star_questions(session, profile, llm, job_id=job_id)
        session.commit()
        assert questions[0].source == "job_specific"
        assert questions[0].job_id == job_id


def test_generate_star_questions_rejects_a_job_from_another_profile(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        try:
            generate_star_questions(session, profile, FakeLLM("not json"), job_id=999)
            assert False, "expected a ValueError for a missing job"
        except ValueError as exc:
            assert "not found" in str(exc)


def test_story_draft_persists_and_review_never_invents_content_on_llm_failure(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        questions = generate_star_questions(session, profile, FakeLLM("not json"))
        session.commit()
        question_id = questions[0].id

        story = get_or_create_story(session, profile.id, question_id)
        session.commit()
        story_id = story.id

        save_story_draft(session, profile.id, story_id, "Led a migration.", "Cut downtime.", "Staged rollout.", "Zero incidents.")
        session.commit()

        result = review_story(session, profile.id, story_id, FakeLLM("not json"))
        session.commit()
        assert result.evaluator == "deterministic-fallback"
        tightened = json.loads(result.tightened_json)
        assert tightened == {
            "situation": "Led a migration.", "task": "Cut downtime.",
            "action": "Staged rollout.", "result": "Zero incidents.",
        }


def test_review_story_requires_at_least_one_written_component(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        questions = generate_star_questions(session, profile, FakeLLM("not json"))
        session.commit()
        story = get_or_create_story(session, profile.id, questions[0].id)
        session.commit()
        try:
            review_story(session, profile.id, story.id, FakeLLM("not json"))
            assert False, "expected a ValueError for an empty story"
        except ValueError as exc:
            assert "Write at least one" in str(exc)


def test_prep_page_and_star_routes_round_trip(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    monkeypatch.setattr(app_module, "build_llm_client", lambda cfg: FakeLLM("not json"))
    client = TestClient(create_app(settings))

    empty_page = client.get("/prep").text
    assert "No practice questions yet" in empty_page

    generated = client.post("/api/star/questions/generate", json={})
    assert generated.status_code == 201
    question_id = generated.json()["questions"][0]["id"]

    started = client.post(f"/api/star/questions/{question_id}/story")
    assert started.status_code == 200
    story_id = started.json()["story"]["id"]

    saved = client.patch(
        f"/api/star/stories/{story_id}",
        json={"situation": "S", "task": "T", "action": "A", "result": "Cut latency 40%."},
    )
    assert saved.status_code == 200
    assert saved.json()["story"]["result"] == "Cut latency 40%."

    completed = client.post(f"/api/star/stories/{story_id}/status", json={"status": "complete"})
    assert completed.status_code == 200
    assert completed.json()["story"]["status"] == "complete"

    page = client.get("/prep").text
    assert "pill-complete" in page
    assert "Cut latency 40%." in page

    reverted = client.post(f"/api/star/stories/{story_id}/status", json={"status": "draft"})
    assert reverted.json()["story"]["status"] == "draft"

    bad_status = client.post(f"/api/star/stories/{story_id}/status", json={"status": "bogus"})
    assert bad_status.status_code == 400


def test_prep_page_shows_a_job_specific_generate_button_for_a_valid_job(tmp_path):
    """Regression test: generate_star_questions() already supported weighting
    toward a job_id, but nothing in the UI ever reached that path -- the
    Prep page always posted {} with no job context. A job_id query param on
    /prep must surface a job-specific generate action, not just the generic
    one, and must ignore/decline a job that isn't the active profile's."""
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        job = JobPosting(
            profile_id=profile.id, source="test", external_id="1", company="Acme",
            title="Staff Engineer", url="https://example.test/1",
        )
        session.add(job)
        session.commit()
        job_id = job.id

    client = TestClient(create_app(settings))
    page = client.get(f"/prep?job_id={job_id}").text
    assert "Staff Engineer @ Acme" in page
    assert 'data-job-id="%d"' % job_id in page

    no_job_page = client.get("/prep").text
    assert "Generate questions for the" not in no_job_page

    bogus_page = client.get("/prep?job_id=999999").text
    assert "Generate questions for the" not in bogus_page


def test_job_specific_question_links_back_to_the_job(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        job = JobPosting(
            profile_id=profile.id, source="test", external_id="1", company="Acme",
            title="Staff Engineer", url="https://example.test/1", description="Own the roadmap.",
        )
        session.add(job)
        session.commit()
        job_id = job.id

    llm = FakeLLM(json.dumps({"questions": [
        {"question": "Tell me about owning a roadmap.", "competency": "leadership"},
    ]}))
    monkeypatch.setattr(app_module, "build_llm_client", lambda cfg: llm)
    client = TestClient(create_app(settings))

    generated = client.post("/api/star/questions/generate", json={"job_id": job_id})
    assert generated.status_code == 201
    question = generated.json()["questions"][0]
    assert question["source"] == "job_specific"
    assert question["job"] == {"id": job_id, "title": "Staff Engineer", "company": "Acme"}

    page = client.get("/prep").text
    assert f'href="/#job-{job_id}"' in page
    assert "job-specific: Staff Engineer" in page


def test_interviewing_job_shows_a_practice_link_on_jobs_and_applications_pages(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        job = JobPosting(
            profile_id=profile.id, source="test", external_id="1", company="Acme",
            title="Staff Engineer", url="https://example.test/1",
            status=ApplicationStatus.INTERVIEWING,
        )
        other = JobPosting(
            profile_id=profile.id, source="test", external_id="2", company="Beta",
            title="Backend Engineer", url="https://example.test/2",
            status=ApplicationStatus.APPLIED,
        )
        session.add_all([job, other])
        session.commit()
        job_id = job.id

    client = TestClient(create_app(settings))
    jobs_page = client.get("/").text
    assert f'href="/prep?job_id={job_id}"' in jobs_page

    applications_page = client.get("/applications").text
    assert f'href="/prep?job_id={job_id}"' in applications_page
    # The applied-but-not-interviewing job must not get the same link.
    assert applications_page.count("Practice for this interview") == 1
