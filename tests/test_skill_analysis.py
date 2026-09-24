import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from jobcopilot.config import Settings
from jobcopilot.dashboard.app import create_app
from jobcopilot.db import get_or_create_profile, make_session_factory
from jobcopilot.models import JobPosting, ProvenSkill, Skill, SkillGapStatus
from jobcopilot.skill_analysis import analyze_job


class FakeLLM:
    def __init__(self, response):
        self.response = response

    def complete_json(self, system, user):
        return self.response


def _settings(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    settings.llm.provider = "none"
    return settings


def test_analyze_job_persists_requirements_and_compares_proven_skills(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        profile.resume_summary_json = json.dumps({"skills": ["Python"]})
        job = JobPosting(
            profile_id=profile.id,
            source="test",
            external_id="1",
            company="Acme",
            title="Backend Engineer",
            url="https://example.test/1",
            description="Required: Python and Kubernetes. SQL is a nice to have.",
        )
        session.add(job)
        session.commit()

        result = analyze_job(session, profile, job, FakeLLM("not json"))
        session.commit()

        assert result["source"] == "deterministic"
        assert result["gap_counts"] == {"satisfied": 1, "missing": 2}
        assert {item["skill"]["slug"] for item in result["requirements"]} == {
            "python",
            "kubernetes",
            "sql",
        }
        assert session.execute(select(Skill)).scalars().all()


def test_api_returns_only_analyzed_saved_jobs_and_does_not_change_fit(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        job = JobPosting(
            profile_id=profile.id,
            source="test",
            external_id="1",
            company="Acme",
            title="Data Engineer",
            url="https://example.test/1",
            description="Python and SQL required.",
            fit_score=73,
        )
        session.add(job)
        session.commit()
        job_id = job.id

    client = TestClient(create_app(settings))
    response = client.post(f"/api/jobs/{job_id}/skill-gaps/analyze")
    assert response.status_code == 200
    assert response.json()["gap_counts"]["missing"] == 2
    listed = client.get("/api/skill-gaps")
    assert listed.status_code == 200
    assert listed.json()["jobs"][0]["job"]["fit_score"] == 73

    with factory() as session:
        stored = session.get(JobPosting, job_id)
        assert stored.fit_score == 73
        assert session.execute(select(ProvenSkill)).scalars().all() == []


def test_job_gap_detail_api_distinguishes_unanalyzed_and_analyzed_jobs(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        job = JobPosting(
            profile_id=profile.id,
            source="test",
            external_id="1",
            company="Acme",
            title="Backend Engineer",
            url="https://example.test/1",
            description="Python required.",
        )
        session.add(job)
        session.commit()
        job_id = job.id

    client = TestClient(create_app(settings))
    before = client.get(f"/api/jobs/{job_id}/skill-gaps")
    assert before.status_code == 200
    assert before.json()["analyzed"] is False

    analyzed = client.post(f"/api/jobs/{job_id}/skill-gaps/analyze")
    assert analyzed.status_code == 200
    after = client.get(f"/api/jobs/{job_id}/skill-gaps")
    assert after.json()["analyzed"] is True
    assert after.json()["gaps"][0]["skill"]["name"] == "python"


def test_index_shows_gap_indicator_and_preserves_status_action(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        profile.resume_summary_json = json.dumps({"skills": ["Python"]})
        job = JobPosting(
            profile_id=profile.id,
            source="test",
            external_id="1",
            company="Acme",
            title="Backend Engineer",
            url="https://example.test/1",
            description="Python and Kubernetes required.",
            status="new",
        )
        session.add(job)
        session.commit()
        job_id = job.id
        analyze_job(session, profile, job, FakeLLM("not json"))
        session.commit()

    html = TestClient(create_app(settings)).get("/").text
    assert "1 gap" in html
    assert "View fit analysis" in html
    assert f"/jobs/{job_id}/status" in html
