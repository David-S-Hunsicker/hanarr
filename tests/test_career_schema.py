import datetime as dt

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from jobcopilot.models import (
    Base,
    JobPosting,
    JobSkill,
    JobSkillRequirement,
    Profile,
    ProfileSkill,
    Project,
    ProjectEvaluation,
    ProjectMode,
    ProjectSkill,
    ProjectSubmission,
    ProjectSubmissionStatus,
    ProvenSkill,
    ResumeProposal,
    ResumeProposalStatus,
    ResumeVersion,
    Skill,
    SkillGapStatus,
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_normalized_skill_and_gap_records_preserve_evidence():
    with _session() as session:
        profile = Profile(name="Candidate")
        job = JobPosting(
            profile=profile,
            source="test",
            external_id="job-1",
            company="Acme",
            title="Engineer",
            url="https://example.com/job-1",
        )
        skill = Skill(name="Python", slug="python")
        session.add_all([profile, job, skill])
        session.flush()
        session.add_all(
            [
                ProfileSkill(
                    profile_id=profile.id,
                    skill_id=skill.id,
                    proficiency=0.8,
                    evidence="Built the ingestion service",
                    confidence=0.95,
                ),
                JobSkill(
                    job_id=job.id,
                    skill_id=skill.id,
                    requirement=JobSkillRequirement.REQUIRED,
                    gap_status=SkillGapStatus.SATISFIED,
                    evidence="Python required in the responsibilities",
                    rationale="Profile evidence matches the requirement",
                    analyzed_at=dt.datetime.utcnow(),
                ),
            ]
        )
        session.commit()

        stored = session.execute(select(JobSkill)).scalar_one()
        assert stored.skill.slug == "python"
        assert stored.gap_status is SkillGapStatus.SATISFIED
        assert stored.evidence.startswith("Python required")


def test_project_submission_evaluation_proves_skill_and_proposes_resume():
    with _session() as session:
        profile = Profile(name="Candidate")
        skill = Skill(name="SQL", slug="sql")
        project = Project(
            profile=profile,
            title="Query optimization project",
            mode=ProjectMode.REUSABLE_SKILL,
            target_outcome="Reduce report latency",
        )
        project.skills.append(ProjectSkill(skill=skill, target_level=0.8))
        submission = ProjectSubmission(
            project=project,
            content="Before/after query plan and benchmark",
            status=ProjectSubmissionStatus.SUBMITTED,
        )
        evaluation = ProjectEvaluation(
            submission=submission,
            passed=True,
            score=0.9,
            feedback="Clear measured improvement",
        )
        session.add_all([profile, skill, project, submission, evaluation])
        session.flush()
        session.add(
            ProvenSkill(
                profile_id=profile.id,
                skill_id=skill.id,
                project_id=project.id,
                evaluation_id=evaluation.id,
                evidence="Passed the optimization project",
            )
        )
        base = ResumeVersion(profile=profile, content="Original resume", is_active=True)
        proposal = ResumeProposal(
            profile=profile,
            base_version=base,
            project_id=project.id,
            proposed_content="Original resume\nAdded SQL optimization outcome",
            diff="+ SQL optimization outcome",
        )
        session.add_all([base, proposal])
        session.commit()

        stored = session.execute(select(ResumeProposal)).scalar_one()
        assert stored.status is ResumeProposalStatus.PENDING
        assert stored.base_version.is_active is True
        assert session.execute(select(ProvenSkill)).scalar_one().evaluation_id == evaluation.id
