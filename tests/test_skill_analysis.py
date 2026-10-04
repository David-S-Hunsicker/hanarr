import datetime as dt
import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from hanarr.config import Settings
from hanarr.dashboard.app import create_app
from hanarr.db import get_or_create_profile, make_session_factory
from hanarr.models import (
    JobPosting,
    JobSkill,
    JobSkillRequirement,
    ProfileSkill,
    Project,
    ProjectEvaluation,
    ProjectMode,
    ProjectSkill,
    ProvenSkill,
    ResumeVersion,
    Skill,
    SkillGapStatus,
)
from hanarr.skill_analysis import analyze_job, saved_job_gaps
from hanarr.skill_analysis import coaching_suggestions, market_demand_summary, profile_skill_page


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


def test_analyzed_saved_job_shows_up_in_saved_job_gaps_without_changing_fit(tmp_path):
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

    with factory() as session:
        profile = get_or_create_profile(session, settings)
        gaps = saved_job_gaps(session, profile)
        assert gaps[0]["job"]["fit_score"] == 73

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


def test_analyze_route_returns_the_real_error_instead_of_a_bare_500(tmp_path, monkeypatch):
    """Regression test: a user reported "Improve my fit" silently
    reverting with no indication of what went wrong -- indistinguishable
    from the button simply not working. Root cause: an unexpected failure
    inside analyze_job() (anything not already absorbed by its own
    LLM-failure fallback -- a DB error, etc.) propagated as a bare 500
    with no detail. The route must catch it and report the actual reason."""
    import hanarr.dashboard.app as app_mod

    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        job = JobPosting(
            profile_id=profile.id, source="test", external_id="1", company="Acme",
            title="Backend Engineer", url="https://example.test/1",
        )
        session.add(job)
        session.commit()
        job_id = job.id

    def boom(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(app_mod, "analyze_job", boom)
    client = TestClient(create_app(settings))

    response = client.post(f"/api/jobs/{job_id}/skill-gaps/analyze")

    assert response.status_code == 500
    assert "database is locked" in response.json()["error"]


def test_draft_cover_letter_route_generates_persists_and_renders_it(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        job = JobPosting(
            profile_id=profile.id, source="test", external_id="1", company="Acme",
            title="Backend Engineer", url="https://example.test/1",
        )
        session.add(job)
        session.commit()
        job_id = job.id

    client = TestClient(create_app(settings))
    response = client.post(f"/api/jobs/{job_id}/cover-letter")

    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "deterministic"  # settings.llm.provider == "none" in _settings
    assert "Acme" in data["cover_letter"]
    assert data["generated_at"] is not None

    html = client.get("/").text
    assert "Cover letter draft" in html
    assert "Redraft cover letter" in html
    assert "Backend Engineer position" in html  # a snippet from the stored draft, HTML-escaping-safe


def test_draft_cover_letter_route_404s_for_a_missing_job(tmp_path):
    settings = _settings(tmp_path)
    client = TestClient(create_app(settings))

    response = client.post("/api/jobs/999999/cover-letter")

    assert response.status_code == 404


def test_draft_outreach_email_route_generates_persists_and_renders_it(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        job = JobPosting(
            profile_id=profile.id, source="test", external_id="1", company="Acme",
            title="Backend Engineer", url="https://example.test/1",
        )
        session.add(job)
        session.commit()
        job_id = job.id

    client = TestClient(create_app(settings))
    response = client.post(f"/api/jobs/{job_id}/outreach", json={"contact": "Jane Doe"})

    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "deterministic"  # settings.llm.provider == "none" in _settings
    assert data["contact"] == "Jane Doe"
    assert "Acme" in data["outreach_email"]
    assert data["generated_at"] is not None

    html = client.get("/").text
    assert "Outreach email draft to Jane Doe" in html
    assert "Redraft outreach email" in html


def test_draft_outreach_email_route_requires_a_contact(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        job = JobPosting(
            profile_id=profile.id, source="test", external_id="1", company="Acme",
            title="Backend Engineer", url="https://example.test/1",
        )
        session.add(job)
        session.commit()
        job_id = job.id

    client = TestClient(create_app(settings))
    response = client.post(f"/api/jobs/{job_id}/outreach", json={"contact": "   "})

    assert response.status_code == 400


def test_draft_outreach_email_route_404s_for_a_missing_job(tmp_path):
    settings = _settings(tmp_path)
    client = TestClient(create_app(settings))

    response = client.post("/api/jobs/999999/outreach", json={"contact": "Jane Doe"})

    assert response.status_code == 404


def test_draft_outreach_email_route_404s_for_another_profiles_job(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile_a = get_or_create_profile(session, settings)
        job = JobPosting(
            profile_id=profile_a.id, source="test", external_id="1", company="Acme",
            title="Backend Engineer", url="https://example.test/1",
        )
        session.add(job)
        session.commit()
        job_id = job.id

    client = TestClient(create_app(settings))
    created = client.post("/profiles", data={"name": "Profile B"}, follow_redirects=False)
    client.cookies.set("hanarr_profile_id", created.cookies["hanarr_profile_id"])

    response = client.post(f"/api/jobs/{job_id}/outreach", json={"contact": "Jane Doe"})

    assert response.status_code == 404


def test_saved_job_gaps_flags_stale_when_active_resume_is_newer_than_the_analysis(tmp_path):
    """If a coaching project gets completed and its resume proposal
    approved, the active resume version changes -- any skill-gap analysis
    run before that point no longer reflects the candidate. The dashboard
    uses this "stale" flag to bring back "Improve my fit" instead of only
    showing the (now possibly outdated) existing analysis."""
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        job = JobPosting(
            profile_id=profile.id, source="test", external_id="1", company="Acme",
            title="Backend Engineer", url="https://example.test/1",
        )
        session.add(job)
        skill = Skill(name="python", slug="python")
        session.add(skill)
        session.commit()

        analyzed_at = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc).replace(tzinfo=None)
        session.add(JobSkill(
            job_id=job.id, skill_id=skill.id, requirement=JobSkillRequirement.REQUIRED,
            gap_status=SkillGapStatus.SATISFIED, analyzed_at=analyzed_at,
        ))
        # The active resume version was created AFTER the analysis ran --
        # e.g. a project's resume proposal was approved afterward.
        session.add(ResumeVersion(
            profile_id=profile.id, content="updated resume", is_active=True,
            created_at=dt.datetime(2026, 2, 1, tzinfo=dt.timezone.utc).replace(tzinfo=None),
        ))
        session.commit()

        gaps = saved_job_gaps(session, profile)
        assert len(gaps) == 1
        assert gaps[0]["stale"] is True


def test_saved_job_gaps_not_stale_when_analysis_is_newer_than_the_active_resume(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        job = JobPosting(
            profile_id=profile.id, source="test", external_id="1", company="Acme",
            title="Backend Engineer", url="https://example.test/1",
        )
        session.add(job)
        skill = Skill(name="python", slug="python")
        session.add(skill)
        session.commit()

        session.add(ResumeVersion(
            profile_id=profile.id, content="resume", is_active=True,
            created_at=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc).replace(tzinfo=None),
        ))
        session.add(JobSkill(
            job_id=job.id, skill_id=skill.id, requirement=JobSkillRequirement.REQUIRED,
            gap_status=SkillGapStatus.SATISFIED,
            analyzed_at=dt.datetime(2026, 2, 1, tzinfo=dt.timezone.utc).replace(tzinfo=None),
        ))
        session.commit()

        gaps = saved_job_gaps(session, profile)
        assert gaps[0]["stale"] is False


def test_saved_job_gaps_not_stale_when_there_is_no_active_resume_version(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        job = JobPosting(
            profile_id=profile.id, source="test", external_id="1", company="Acme",
            title="Backend Engineer", url="https://example.test/1",
        )
        session.add(job)
        skill = Skill(name="python", slug="python")
        session.add(skill)
        session.commit()
        session.add(JobSkill(
            job_id=job.id, skill_id=skill.id, requirement=JobSkillRequirement.REQUIRED,
            gap_status=SkillGapStatus.SATISFIED, analyzed_at=dt.datetime(2026, 1, 1),
        ))
        session.commit()

        gaps = saved_job_gaps(session, profile)
        assert gaps[0]["stale"] is False


def test_index_shows_improve_my_fit_again_when_the_analysis_is_stale(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        job = JobPosting(
            profile_id=profile.id, source="test", external_id="1", company="Acme",
            title="Backend Engineer", url="https://example.test/1", fit_score=80.0,
        )
        session.add(job)
        skill = Skill(name="python", slug="python")
        session.add(skill)
        session.commit()
        session.add(JobSkill(
            job_id=job.id, skill_id=skill.id, requirement=JobSkillRequirement.REQUIRED,
            gap_status=SkillGapStatus.SATISFIED,
            analyzed_at=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc).replace(tzinfo=None),
        ))
        session.add(ResumeVersion(
            profile_id=profile.id, content="updated resume", is_active=True,
            created_at=dt.datetime(2026, 2, 1, tzinfo=dt.timezone.utc).replace(tzinfo=None),
        ))
        session.commit()

    html = TestClient(create_app(settings)).get("/").text
    assert "Improve my fit" in html
    assert "Your resume has changed since this was run" in html


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
    assert 'document.querySelectorAll(".project-action").forEach' in html
    assert f'id="job-{job_id}"' in html
    assert "View on Skills page" in html
    assert "Apply &#8599;" in html
    assert '<div class="job-company">Acme</div>' in html
    assert "Preferences &amp; config" not in html


def test_skills_page_separates_capability_project_resume_and_job_evidence(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        profile.resume_summary_json = json.dumps({"skills": ["Python"]})
        skill = Skill(name="Python", slug="python")
        session.add(skill)
        session.flush()
        session.add(ProfileSkill(
            profile_id=profile.id, skill_id=skill.id, proficiency=.8,
            confidence=.6, evidence="Maintained a production API", source="manual",
        ))
        project = Project(
            profile_id=profile.id, title="Python API project",
            mode=ProjectMode.REUSABLE_SKILL, target_outcome="Working API",
        )
        project.skills.append(ProjectSkill(skill_id=skill.id, target_level=.9))
        job = JobPosting(
            profile_id=profile.id, source="test", external_id="1",
            company="Acme", title="Python Engineer", url="https://example.test",
        )
        session.add_all([project, job])
        session.commit()
        skill_id = skill.id
        job_id = job.id
        analyze_job(session, profile, job, FakeLLM('{"requirements": [{"name": "Python"}]}'))
        session.commit()

    client = TestClient(create_app(settings))
    page = client.get("/skills")
    assert page.status_code == 200
    assert "Maintained a production API" in page.text
    assert "Python API project" in page.text
    assert "Affected jobs" in page.text
    assert "Not proven" in page.text
    assert f'id="skill-{skill_id}"' in page.text
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        assert profile_skill_page(session, profile)[0]["proven"] is None

    updated = client.patch(
        f"/api/skills/{skill_id}/profile",
        json={"proficiency": .9, "confidence": .8, "evidence": "Corrected evidence"},
    )
    assert updated.status_code == 200
    assert updated.json()["proven"] is False


def test_skills_page_groups_proven_and_unproven_and_has_a_filter_box(tmp_path):
    """Regression test: with dozens of resume-extracted skills, one full
    card per skill made the page an unscannable wall -- skills must be
    grouped (proven vs not) with a compact collapsed row, and a filter box
    must be present to narrow by name."""
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        profile.resume_summary_json = json.dumps({"skills": ["Python", "SQL"]})
        python_skill = Skill(name="Python", slug="python")
        sql_skill = Skill(name="SQL", slug="sql")
        session.add_all([python_skill, sql_skill])
        session.flush()
        project = Project(
            profile_id=profile.id, title="Python API project",
            mode=ProjectMode.REUSABLE_SKILL, target_outcome="Working API",
        )
        project.skills.append(ProjectSkill(skill_id=python_skill.id, target_level=.9))
        session.add(project)
        session.commit()
        python_skill_id, project_id = python_skill.id, project.id

    with factory() as session:
        profile = get_or_create_profile(session, settings)
        evaluation = ProjectEvaluation(
            submission_id=1, attempt_number=1, evaluator="deterministic", passed=True,
            score=90.0, outcome="passed",
        )
        session.add(evaluation)
        session.flush()
        session.add(ProvenSkill(
            profile_id=profile.id, skill_id=python_skill_id, project_id=project_id,
            evaluation_id=evaluation.id, evidence="Shipped a production API",
        ))
        session.commit()

    client = TestClient(create_app(settings))
    page = client.get("/skills").text
    assert page.count('id="skill-filter"') == 1

    proven_section = page.split("Not proven yet")[0]
    assert "Python" in proven_section
    unproven_section = page.split("Not proven yet")[1]
    assert "SQL" in unproven_section


def test_skills_page_has_quick_confidence_presets(tmp_path):
    """"There should be a way to manually mark a skill or change it...
    some skills the user knows they have high confidence in." A raw 0-1
    decimal correction form buried two <details> deep wasn't discoverable
    -- a one-click Low/Medium/High preset must be directly visible once a
    skill row is expanded, wired to the same underlying proficiency/
    confidence inputs the correction PATCH already uses."""
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        profile.resume_summary_json = json.dumps({"skills": ["Kubernetes"]})
        session.commit()

    page = TestClient(create_app(settings)).get("/skills").text
    assert 'class="preset-btn low" data-level="Low" data-value=".3"' in page
    assert 'class="preset-btn medium" data-level="Medium" data-value=".6"' in page
    assert 'class="preset-btn high" data-level="High" data-value=".9"' in page
    # Exactly one proficiency/confidence <input> per skill -- not two
    # competing fields with the same name that would race in FormData.
    assert page.count('<input name="proficiency"') == 1
    assert page.count('<input name="confidence"') == 1


def test_skill_override_requires_evidence_and_never_creates_proof(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        skill = Skill(name="SQL", slug="sql")
        session.add(skill)
        session.commit()
        skill_id = skill.id

    client = TestClient(create_app(settings))
    missing = client.patch(
        f"/api/skills/{skill_id}/profile",
        json={"proficiency": .5, "confidence": .5},
    )
    assert missing.status_code == 400
    invalid = client.patch(
        f"/api/skills/{skill_id}/profile",
        json={"proficiency": 2, "confidence": .5, "evidence": "No"},
    )
    assert invalid.status_code == 400


def test_market_demand_prioritizes_required_gaps_and_surfaces_manual_correction(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        skill = Skill(name="Python", slug="python")
        session.add(skill)
        session.flush()
        session.add(ProfileSkill(
            profile_id=profile.id, skill_id=skill.id, proficiency=.8,
            confidence=.9, evidence="User corrected capability level", source="manual",
        ))
        jobs = [
            JobPosting(profile_id=profile.id, source="test", external_id=str(i),
                       company="Co", title=f"Engineer {i}", url=f"https://example.test/{i}")
            for i in (1, 2)
        ]
        session.add_all(jobs)
        session.flush()
        session.add_all([
            JobSkill(job_id=jobs[0].id, skill_id=skill.id,
                     requirement=JobSkillRequirement.REQUIRED, gap_status=SkillGapStatus.MISSING),
            JobSkill(job_id=jobs[1].id, skill_id=skill.id,
                     requirement=JobSkillRequirement.PREFERRED, gap_status=SkillGapStatus.PARTIAL),
        ])
        session.commit()

        summary = market_demand_summary(session, profile)

    assert summary[0]["affected_job_count"] == 2
    assert summary[0]["required_count"] == 1
    assert summary[0]["preferred_count"] == 1
    assert summary[0]["gap_count"] == 2
    assert summary[0]["estimated_effort"] == "medium"
    assert summary[0]["user_correction"]["evidence"] == "User corrected capability level"


def test_coaching_suggestions_and_pages_expose_reusable_market_priorities(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        job = JobPosting(
            profile_id=profile.id, source="test", external_id="market",
            company="Acme", title="Backend Engineer", url="https://example.test/market",
        )
        skill = Skill(name="Python", slug="python")
        session.add_all([job, skill])
        session.flush()
        session.add(JobSkill(
            job_id=job.id, skill_id=skill.id,
            requirement=JobSkillRequirement.REQUIRED, gap_status=SkillGapStatus.MISSING,
        ))
        session.commit()
        job_id, skill_id = job.id, skill.id

    with factory() as session:
        profile = get_or_create_profile(session, settings)
        suggestions = coaching_suggestions(session, profile)
        demand = market_demand_summary(session, profile)
    assert suggestions[0]["skill"]["id"] == skill_id
    assert demand[0]["affected_job_ids"] == [job_id]

    client = TestClient(create_app(settings))
    assert "Market demand" in client.get("/coaching").text
    assert "Market priority" in client.get("/skills").text
