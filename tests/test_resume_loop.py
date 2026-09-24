import json

from fastapi.testclient import TestClient
from sqlalchemy import select

import jobcopilot.dashboard.app as app_module
from jobcopilot.config import Settings
from jobcopilot.dashboard.app import create_app
from jobcopilot.db import get_or_create_profile, make_session_factory
from jobcopilot.models import (
    JobPosting,
    JobSkill,
    JobSkillRequirement,
    Project,
    ProjectStatus,
    ResumeProposal,
    ScoreSnapshot,
    Skill,
    SkillGapStatus,
    ResumeVersion,
)


class PassingLLM:
    def complete_json(self, system, user):
        if "Evaluate a coaching project" in system:
            return json.dumps({
                "outcome": "passed", "score": 90, "scores": {"criterion": 90},
                "strengths": ["Evidence"], "improvements": [], "actionable_feedback": [],
                "feedback": "Passed.",
            })
        raise RuntimeError("deterministic proposal fallback")


def test_pass_generates_pending_proposal_and_approval_rescores_affected_job(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path / "data")
    settings.llm.provider = "none"
    monkeypatch.setattr(app_module, "build_llm_client", lambda cfg: PassingLLM())
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        profile.resume_text = "Backend engineer with Python."
        job = JobPosting(
            profile_id=profile.id, source="test", external_id="1", company="Acme",
            title="Backend Engineer", url="https://example.test/1", description="Python",
            fit_score=60, fit_rationale="Before",
        )
        skill = Skill(name="Python", slug="python")
        session.add_all([job, skill])
        session.flush()
        session.add(JobSkill(
            job_id=job.id, skill_id=skill.id, requirement=JobSkillRequirement.REQUIRED,
            gap_status=SkillGapStatus.MISSING,
        ))
        session.commit()
        job_id = job.id
        skill_id = skill.id

    client = TestClient(create_app(settings))
    project = client.post("/api/coaching-projects", json={
        "mode": "posting_specific", "job_id": job_id, "skill_id": skill_id,
    }).json()
    submission = client.post(
        f"/api/coaching-projects/{project['id']}/submissions",
        json={"title": "Evidence", "content": "Built and tested the project."},
    ).json()
    client.post(f"/api/coaching-projects/{project['id']}/submissions/{submission['id']}/submit")
    evaluated = client.post(
        f"/api/coaching-projects/{project['id']}/submissions/{submission['id']}/evaluate"
    )
    assert evaluated.status_code == 200
    proposal_id = evaluated.json()["resume_proposal_id"]

    with factory() as session:
        proposal = session.get(ResumeProposal, proposal_id)
        assert proposal.status.value == "pending"
        assert session.get(Project, project["id"]).status is ProjectStatus.COMPLETED

    approved = client.post(f"/api/resume/proposals/{proposal_id}/approve")
    assert approved.status_code == 200
    assert approved.json()["affected_jobs"][0]["job_id"] == job_id
    with factory() as session:
        snapshots = session.scalars(select(ScoreSnapshot).where(ScoreSnapshot.job_id == job_id)).all()
        assert snapshots[-1].trigger == "resume_approved"
        assert session.get(ResumeProposal, proposal_id).status.value == "approved"
    assert client.get("/resume").status_code == 200
    assert client.get("/api/resume").json()["active"]["content"]


def test_resume_page_explains_matcher_source_profile_and_score_impact(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    settings.llm.provider = "none"
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        profile.resume_text = "Backend engineer with Python."
        profile.resume_summary_json = json.dumps({
            "titles": ["Backend Engineer"], "years_experience": 8,
            "skills": ["Python", "SQL"], "industries": ["SaaS"],
            "seniority": "senior", "summary": "Builds reliable services.",
        })
        version = ResumeVersion(profile_id=profile.id, content=profile.resume_text, is_active=True)
        session.add(version)
        session.flush()
        session.add(JobPosting(
            profile_id=profile.id, source="test", external_id="impact",
            company="Acme", title="Platform Engineer", url="https://example.test/impact",
            fit_score=84,
        ))
        session.commit()

    response = TestClient(create_app(settings)).get("/resume")
    assert response.status_code == 200
    assert "feeds new job matching and rescoring" in response.text
    assert "Backend Engineer" in response.text
    assert "Builds reliable services." in response.text
    assert "No approved resume has been rematched yet." in response.text
