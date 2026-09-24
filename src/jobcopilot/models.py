"""SQLAlchemy models.

Schema is deliberately profile-scoped (every table hangs off `Profile`)
even though v1 only ever creates one profile per instance. That's the
seam a future multi-tenant version would extend rather than rewrite —
today it just means "the one person running this install."
"""
from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, default="Your Name")
    resume_text: Mapped[str] = mapped_column(Text, default="")
    resume_summary_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow
    )

    jobs: Mapped[list["JobPosting"]] = relationship(back_populates="profile")
    reminders: Mapped[list["Reminder"]] = relationship(back_populates="profile")
    profile_skills: Mapped[list["ProfileSkill"]] = relationship(back_populates="profile")
    projects: Mapped[list["Project"]] = relationship(back_populates="profile")
    resume_versions: Mapped[list["ResumeVersion"]] = relationship(back_populates="profile")
    resume_proposals: Mapped[list["ResumeProposal"]] = relationship(back_populates="profile")


class ApplicationStatus(str, enum.Enum):
    NEW = "new"
    REVIEWED = "reviewed"
    APPLIED = "applied"
    INTERVIEWING = "interviewing"
    OFFER = "offer"
    REJECTED = "rejected"
    DISMISSED = "dismissed"  # user marked "not interested"


class JobPosting(Base):
    __tablename__ = "job_postings"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_source_external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"))

    source: Mapped[str] = mapped_column(String)           # "greenhouse", "remoteok", ...
    external_id: Mapped[str] = mapped_column(String)      # id/slug from the source
    company: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    location: Mapped[str] = mapped_column(String, default="")
    remote: Mapped[bool] = mapped_column(Boolean, default=False)
    url: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(Text, default="")
    salary_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    posted_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    fetched_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    fit_score: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0-100
    fit_rationale: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[ApplicationStatus] = mapped_column(
        Enum(ApplicationStatus), default=ApplicationStatus.NEW
    )
    status_changed_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=dt.datetime.utcnow
    )

    profile: Mapped["Profile"] = relationship(back_populates="jobs")
    reminders: Mapped[list["Reminder"]] = relationship(back_populates="job")
    job_skills: Mapped[list["JobSkill"]] = relationship(back_populates="job")


class SeenPosting(Base):
    """Records that a posting was fetched and scored, whether or not it
    cleared min_fit_score — lets the pipeline skip re-fetching-and-rescoring
    the same posting on every search cycle. Deliberately minimal (no title,
    description, etc.): postings that passed are already fully captured in
    JobPosting, so this table only exists to avoid redundant LLM calls on
    postings that didn't. Note: like JobPosting's own uniqueness, a posting
    seen once stays seen even if min_fit_score or preferences change later —
    it won't be automatically re-scored under new criteria.
    """

    __tablename__ = "seen_postings"
    __table_args__ = (UniqueConstraint("profile_id", "source", "external_id", name="uq_seen_source_external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"))
    source: Mapped[str] = mapped_column(String)
    external_id: Mapped[str] = mapped_column(String)
    seen_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class ReminderType(str, enum.Enum):
    FOLLOW_UP = "follow_up"
    INTERVIEW_PREP = "interview_prep"
    CUSTOM = "custom"


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"))
    job_id: Mapped[int | None] = mapped_column(ForeignKey("job_postings.id"), nullable=True)

    type: Mapped[ReminderType] = mapped_column(Enum(ReminderType), default=ReminderType.CUSTOM)
    message: Mapped[str] = mapped_column(Text)
    due_at: Mapped[dt.datetime] = mapped_column(DateTime)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    profile: Mapped["Profile"] = relationship(back_populates="reminders")
    job: Mapped["JobPosting | None"] = relationship(back_populates="reminders")


class ScoreSnapshot(Base):
    """Immutable record of a fit score and the explanation shown to the user."""

    __tablename__ = "score_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"))
    job_id: Mapped[int] = mapped_column(ForeignKey("job_postings.id"))
    fit_score: Mapped[float] = mapped_column(Float)
    fit_rationale: Mapped[str] = mapped_column(Text, default="")
    trigger: Mapped[str] = mapped_column(String, default="initial")
    scorer_metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class Skill(Base):
    """Canonical capability shared by profile, job, and project records."""

    __tablename__ = "skills"
    __table_args__ = (UniqueConstraint("slug", name="uq_skill_slug"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    slug: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    profile_skills: Mapped[list["ProfileSkill"]] = relationship(back_populates="skill")
    job_skills: Mapped[list["JobSkill"]] = relationship(back_populates="skill")
    project_skills: Mapped[list["ProjectSkill"]] = relationship(back_populates="skill")
    proven_skills: Mapped[list["ProvenSkill"]] = relationship(back_populates="skill")


class ProfileSkill(Base):
    __tablename__ = "profile_skills"
    __table_args__ = (UniqueConstraint("profile_id", "skill_id", name="uq_profile_skill"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"))
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id"))
    proficiency: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String, default="manual")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow
    )

    profile: Mapped["Profile"] = relationship(back_populates="profile_skills")
    skill: Mapped["Skill"] = relationship(back_populates="profile_skills")


class JobSkillRequirement(str, enum.Enum):
    REQUIRED = "required"
    PREFERRED = "preferred"


class SkillGapStatus(str, enum.Enum):
    UNKNOWN = "unknown"
    MISSING = "missing"
    PARTIAL = "partial"
    SATISFIED = "satisfied"


class JobSkill(Base):
    __tablename__ = "job_skills"
    __table_args__ = (UniqueConstraint("job_id", "skill_id", name="uq_job_skill"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("job_postings.id"))
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id"))
    requirement: Mapped[JobSkillRequirement] = mapped_column(
        Enum(JobSkillRequirement), default=JobSkillRequirement.REQUIRED
    )
    gap_status: Mapped[SkillGapStatus] = mapped_column(
        Enum(SkillGapStatus), default=SkillGapStatus.UNKNOWN
    )
    evidence: Mapped[str] = mapped_column(Text, default="")
    rationale: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    analyzed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    job: Mapped["JobPosting"] = relationship(back_populates="job_skills")
    skill: Mapped["Skill"] = relationship(back_populates="job_skills")


class ProjectMode(str, enum.Enum):
    POSTING_SPECIFIC = "posting_specific"
    REUSABLE_SKILL = "reusable_skill"


class ProjectStatus(str, enum.Enum):
    PLANNED = "planned"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"))
    job_id: Mapped[int | None] = mapped_column(ForeignKey("job_postings.id"), nullable=True)
    title: Mapped[str] = mapped_column(String)
    mode: Mapped[ProjectMode] = mapped_column(Enum(ProjectMode))
    status: Mapped[ProjectStatus] = mapped_column(Enum(ProjectStatus), default=ProjectStatus.PLANNED)
    description: Mapped[str] = mapped_column(Text, default="")
    target_outcome: Mapped[str] = mapped_column(Text, default="")
    opted_in_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    profile: Mapped["Profile"] = relationship(back_populates="projects")
    job: Mapped["JobPosting | None"] = relationship()
    skills: Mapped[list["ProjectSkill"]] = relationship(back_populates="project")
    submissions: Mapped[list["ProjectSubmission"]] = relationship(back_populates="project")


class ProjectSkill(Base):
    __tablename__ = "project_skills"
    __table_args__ = (UniqueConstraint("project_id", "skill_id", name="uq_project_skill"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id"))
    target_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence: Mapped[str] = mapped_column(Text, default="")

    project: Mapped["Project"] = relationship(back_populates="skills")
    skill: Mapped["Skill"] = relationship(back_populates="project_skills")


class ProjectSubmissionStatus(str, enum.Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    EVALUATED = "evaluated"


class ProjectSubmission(Base):
    __tablename__ = "project_submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[ProjectSubmissionStatus] = mapped_column(
        Enum(ProjectSubmissionStatus), default=ProjectSubmissionStatus.DRAFT
    )
    submitted_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    project: Mapped["Project"] = relationship(back_populates="submissions")
    evaluations: Mapped[list["ProjectEvaluation"]] = relationship(back_populates="submission")


class ProjectEvaluation(Base):
    __tablename__ = "project_evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("project_submissions.id"))
    evaluator: Mapped[str] = mapped_column(String, default="manual")
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    feedback: Mapped[str] = mapped_column(Text, default="")
    evaluated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    submission: Mapped["ProjectSubmission"] = relationship(back_populates="evaluations")


class ProvenSkill(Base):
    __tablename__ = "proven_skills"
    __table_args__ = (UniqueConstraint("profile_id", "skill_id", name="uq_proven_profile_skill"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"))
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id"))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    evaluation_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_evaluations.id"), nullable=True
    )
    evidence: Mapped[str] = mapped_column(Text, default="")
    proven_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    skill: Mapped["Skill"] = relationship(back_populates="proven_skills")


class ResumeProposalStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ResumeVersion(Base):
    __tablename__ = "resume_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"))
    content: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    profile: Mapped["Profile"] = relationship(back_populates="resume_versions")
    proposals: Mapped[list["ResumeProposal"]] = relationship(
        back_populates="base_version", foreign_keys="ResumeProposal.base_version_id"
    )


class ResumeProposal(Base):
    __tablename__ = "resume_proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"))
    base_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("resume_versions.id"), nullable=True
    )
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    proposed_content: Mapped[str] = mapped_column(Text)
    diff: Mapped[str] = mapped_column(Text, default="")
    rationale: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[ResumeProposalStatus] = mapped_column(
        Enum(ResumeProposalStatus), default=ResumeProposalStatus.PENDING
    )
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    profile: Mapped["Profile"] = relationship(back_populates="resume_proposals")
    base_version: Mapped["ResumeVersion | None"] = relationship(
        back_populates="proposals", foreign_keys=[base_version_id]
    )
