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
