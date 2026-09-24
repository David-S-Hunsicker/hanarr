import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from jobcopilot.config import Settings
from jobcopilot.dashboard.app import create_app
from jobcopilot.db import get_or_create_profile, make_session_factory
from jobcopilot.models import JobPosting, JobSkill, JobSkillRequirement, Skill, SkillGapStatus, Project


class FakeLLM:
    def __init__(self, response):
        self.response = response

    def complete_json(self, system, user):
        return self.response


def _settings(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    settings.llm.provider = "none"
    return settings


def _analyzed_job(session, profile):
    job = JobPosting(
        profile_id=profile.id, source="test", external_id="1", company="Acme",
        title="Backend Engineer", url="https://example.test/1",
    )
    skill = Skill(name="Python", slug="python")
    session.add_all([job, skill])
    session.flush()
    session.add(JobSkill(
        job_id=job.id, skill_id=skill.id, requirement=JobSkillRequirement.REQUIRED,
        gap_status=SkillGapStatus.MISSING, evidence="Python required",
    ))
    session.commit()
    return job, skill


def test_posting_project_persists_fallback_brief_tasks_and_affected_job(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        job, skill = _analyzed_job(session, profile)
        job_id, skill_id = job.id, skill.id
        client = TestClient(create_app(settings))

    response = client.post("/api/coaching-projects", json={
        "mode": "posting_specific", "job_id": job_id, "skill_id": skill_id,
    })
    assert response.status_code == 201
    body = response.json()
    assert body["mode"] == "posting_specific"
    assert body["brief"]["source"] == "deterministic"
    assert len(body["tasks"]) == 3
    assert body["affected_job_ids"] == [job_id]

    with factory() as session:
        stored = session.execute(select(Project)).scalar_one()
        assert json.loads(stored.brief_json)["target_outcome"]


def test_reusable_skill_project_covers_all_affected_jobs_and_status_api(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        job_one, skill = _analyzed_job(session, profile)
        job_one_id, skill_id = job_one.id, skill.id
        job_two = JobPosting(
            profile_id=profile.id, source="test", external_id="2", company="Beta",
            title="Platform Engineer", url="https://example.test/2",
        )
        session.add(job_two)
        session.flush()
        session.add(JobSkill(
            job_id=job_two.id, skill_id=skill.id, requirement=JobSkillRequirement.PREFERRED,
            gap_status=SkillGapStatus.PARTIAL,
        ))
        session.commit()
        job_two_id = job_two.id

    client = TestClient(create_app(settings))
    response = client.post("/api/coaching-projects", json={
        "mode": "reusable_skill", "skill_id": skill_id,
    })
    assert response.status_code == 201
    body = response.json()
    assert set(body["affected_job_ids"]) == {job_one_id, job_two_id}
    assert client.get("/api/coaching-projects").json()["projects"][0]["id"] == body["id"]
    assert client.get(f"/api/coaching-projects/{body['id']}").json()["status"] == "planned"
