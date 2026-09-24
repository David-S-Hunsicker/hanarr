import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from jobcopilot.config import Settings
from jobcopilot.dashboard.app import create_app
from jobcopilot.db import get_or_create_profile, make_session_factory
from jobcopilot.models import (
    JobPosting,
    JobSkill,
    JobSkillRequirement,
    Project,
    ProjectTaskStatus,
    Skill,
    SkillGapStatus,
)
import jobcopilot.dashboard.app as app_module
import jobcopilot.submissions as submissions_module


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


def test_coaching_page_shows_suggestions_projects_jobs_and_updates_task(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        job, skill = _analyzed_job(session, profile)
        job_id, skill_id = job.id, skill.id
    client = TestClient(create_app(settings))

    created = client.post("/api/coaching-projects", json={
        "mode": "posting_specific", "job_id": job_id, "skill_id": skill_id,
    }).json()
    html = client.get("/coaching").text
    assert "Coaching" in html
    assert "Python" in html
    assert "Backend Engineer" in html
    assert "Affected jobs" in html
    assert f"/#job-{job_id}" in html

    task_id = created["tasks"][0]["id"]
    updated = client.post(
        f"/api/coaching-projects/{created['id']}/tasks/{task_id}/status",
        json={"status": "in_progress"},
    )
    assert updated.status_code == 200
    assert updated.json()["tasks"][0]["status"] == "in_progress"

    with factory() as session:
        project = session.get(Project, created["id"])
        assert project.status.value == "active"
        assert project.tasks[0].status is ProjectTaskStatus.IN_PROGRESS


def test_submission_api_normalizes_written_history_and_requires_explicit_submit(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        job, skill = _analyzed_job(session, profile)
        job_id, skill_id = job.id, skill.id
    client = TestClient(create_app(settings))
    project_id = client.post("/api/coaching-projects", json={
        "mode": "posting_specific", "job_id": job_id, "skill_id": skill_id,
    }).json()["id"]

    created = client.post(
        f"/api/coaching-projects/{project_id}/submissions",
        json={"title": "Reflection", "content": "I built and tested the workflow."},
    )
    assert created.status_code == 201
    assert created.json()["kind"] == "written_response"
    assert created.json()["status"] == "draft"
    assert client.get(f"/api/coaching-projects/{project_id}/submissions").json()["submissions"][0]["status"] == "draft"

    submitted = client.post(
        f"/api/coaching-projects/{project_id}/submissions/{created.json()['id']}/submit"
    )
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "submitted"


def test_local_submission_stores_manifest_and_rejects_traversal(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        job, skill = _analyzed_job(session, profile)
        job_id, skill_id = job.id, skill.id
    client = TestClient(create_app(settings))
    project_id = client.post("/api/coaching-projects", json={
        "mode": "posting_specific", "job_id": job_id, "skill_id": skill_id,
    }).json()["id"]

    created = client.post(
        f"/api/coaching-projects/{project_id}/submissions/files",
        files=[("files", ("nested/README.md", b"evidence", "text/plain"))],
    )
    assert created.status_code == 201
    body = created.json()
    assert body["kind"] == "local_files"
    assert body["manifest"] == [{"path": "nested/README.md", "bytes": 8}]
    assert (tmp_path / "data" / "submissions" / str(project_id) / str(body["id"]) / "nested" / "README.md").read_text() == "evidence"

    rejected = client.post(
        f"/api/coaching-projects/{project_id}/submissions/files",
        files=[("files", ("../secret.txt", b"nope", "text/plain"))],
    )
    assert rejected.status_code == 400


def test_local_submission_streams_and_rejects_oversized_artifact_without_persisting(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        job, skill = _analyzed_job(session, profile)
        job_id, skill_id = job.id, skill.id
    monkeypatch.setattr(submissions_module, "MAX_FILE_BYTES", 3)
    client = TestClient(create_app(settings))
    project_id = client.post("/api/coaching-projects", json={
        "mode": "posting_specific", "job_id": job_id, "skill_id": skill_id,
    }).json()["id"]

    rejected = client.post(
        f"/api/coaching-projects/{project_id}/submissions/files",
        files=[("files", ("large.txt", b"four", "text/plain"))],
    )

    assert rejected.status_code == 413
    with factory() as session:
        assert session.query(Project).first().submissions == []
    assert not list((tmp_path / "data" / "submissions").rglob("*.staging"))
    rejected = client.post(
        f"/api/coaching-projects/{project_id}/submissions/files",
        files=[
            ("files", ("same.txt", b"one", "text/plain")),
            ("files", ("same.txt", b"two", "text/plain")),
        ],
    )
    assert rejected.status_code == 400


def test_github_submission_persists_safe_reference_without_fetching(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        job, skill = _analyzed_job(session, profile)
        job_id, skill_id = job.id, skill.id
    client = TestClient(create_app(settings))
    project_id = client.post("/api/coaching-projects", json={
        "mode": "posting_specific", "job_id": job_id, "skill_id": skill_id,
    }).json()["id"]

    created = client.post(
        f"/api/coaching-projects/{project_id}/submissions/github",
        json={"reference": "https://github.com/example/demo.git", "ref": "main"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["kind"] == "github_repository"
    assert body["content"] == "https://github.com/example/demo@main"
    assert body["metadata"] == {
        "provider": "github",
        "repository_url": "https://github.com/example/demo",
        "ref": "main",
        "fetched": False,
        "execution": False,
    }
    assert body["artifact_dir"] is None

    rejected = client.post(
        f"/api/coaching-projects/{project_id}/submissions/github",
        json={"reference": "https://evil.example/demo", "ref": "main"},
    )
    assert rejected.status_code == 400
    rejected = client.post(
        f"/api/coaching-projects/{project_id}/submissions/github",
        json={"reference": "https://github.com/example/demo?clone=1", "ref": "main"},
    )
    assert rejected.status_code == 400


def test_submission_evaluation_persists_structured_result_and_resubmission_history(tmp_path, monkeypatch):
    settings = _settings(tmp_path)

    class EvaluatingLLM:
        def complete_json(self, system, user):
            return json.dumps({
                "outcome": "passed",
                "score": 88,
                "scores": {"Build the first version": 90},
                "strengths": ["Working artifact is described."],
                "improvements": [],
                "actionable_feedback": [],
                "feedback": "Strong evidence.",
            })

    monkeypatch.setattr(app_module, "build_llm_client", lambda cfg: EvaluatingLLM())
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        job, skill = _analyzed_job(session, profile)
        job_id, skill_id = job.id, skill.id
    client = TestClient(create_app(settings))
    project_id = client.post("/api/coaching-projects", json={
        "mode": "posting_specific", "job_id": job_id, "skill_id": skill_id,
    }).json()["id"]
    submission = client.post(
        f"/api/coaching-projects/{project_id}/submissions",
        json={"title": "Evidence", "content": "Built and tested the artifact."},
    ).json()
    client.post(f"/api/coaching-projects/{project_id}/submissions/{submission['id']}/submit")

    evaluated = client.post(
        f"/api/coaching-projects/{project_id}/submissions/{submission['id']}/evaluate"
    )
    assert evaluated.status_code == 200
    result = evaluated.json()["evaluations"][0]
    assert result["outcome"] == "passed"
    assert result["scores"]["Build the first version"] == 90
    assert result["attempt_number"] == 1
    coaching_html = client.get("/coaching").text
    assert "Attempt 1: passed" in coaching_html
    assert "Revise and resubmit" in coaching_html

    retried = client.post(
        f"/api/coaching-projects/{project_id}/submissions/{submission['id']}/resubmit"
    )
    assert retried.status_code == 200
    assert retried.json()["status"] == "draft"
    client.post(f"/api/coaching-projects/{project_id}/submissions/{submission['id']}/submit")
    second = client.post(
        f"/api/coaching-projects/{project_id}/submissions/{submission['id']}/evaluate"
    ).json()["evaluations"]
    assert [item["attempt_number"] for item in second] == [1, 2]


def test_malformed_evaluation_uses_safe_fallback_and_records_error(tmp_path, monkeypatch):
    settings = _settings(tmp_path)

    class BrokenLLM:
        def complete_json(self, system, user):
            return "not json"

    monkeypatch.setattr(app_module, "build_llm_client", lambda cfg: BrokenLLM())
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        job, skill = _analyzed_job(session, profile)
        job_id, skill_id = job.id, skill.id
    client = TestClient(create_app(settings))
    project = client.post("/api/coaching-projects", json={
        "mode": "posting_specific", "job_id": job_id, "skill_id": skill_id,
    }).json()
    submission = client.post(
        f"/api/coaching-projects/{project['id']}/submissions",
        json={"content": "A short response."},
    ).json()
    client.post(f"/api/coaching-projects/{project['id']}/submissions/{submission['id']}/submit")
    evaluated = client.post(
        f"/api/coaching-projects/{project['id']}/submissions/{submission['id']}/evaluate"
    ).json()
    evaluation = evaluated["evaluations"][0]
    assert evaluation["evaluator"] == "deterministic-fallback"
    assert evaluation["outcome"] == "needs_improvement"
    assert evaluation["error"]
