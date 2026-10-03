"""Coach: an in-app chatbot grounded in the profile's own stored data
(resume, matched jobs, skill gaps, coaching projects) rather than a blank
general-purpose chatbot -- see DEVELOPMENT_LOG.md's phased plan. This module
covers phase 1 only: a persisted conversation and read-only, data-grounded
Q&A. Phase 2 (action requests -- "create a project for X") lives in
coach_actions.py, built on top of this.

Deliberately a bounded full-context summary rather than real
retrieval/embeddings: Hanarr's actual scale (one person's job search,
dozens not millions of rows) means everything relevant fits comfortably in
a modern context window -- a vector index would be solving a scale problem
this app doesn't have.
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from .llm.base import LLMClient
from .models import ChatMessage, ChatRole, JobPosting, Profile, Project, ProjectStatus

SYSTEM_PROMPT = """You are Coach, a career-coaching assistant built into Hanarr, a local-first
job-search app. Answer only from the CONTEXT given in the user message and the conversation so
far -- never invent a fact about the candidate's resume, jobs, skills, or projects that isn't
actually present in CONTEXT. If something isn't in CONTEXT, say plainly that you don't have that
information rather than guessing. Be direct and specific: reference actual job titles, companies,
and skill/project names from CONTEXT instead of vague generalities. You can also explain how
Hanarr's own features work using the HANARR GUIDE section of CONTEXT. Return ONLY JSON in this
shape: {"answer": "..."}"""

# A short, stable summary of how Hanarr works, kept here rather than parsed
# out of guide.html's markup -- a plain-text constant is simpler to keep in
# sync than scraping a Jinja template, and this only needs to be "close
# enough to explain the app," not a verbatim copy of the Guide page.
HANARR_GUIDE_SUMMARY = """Hanarr tracks a job search end to end: Jobs (matched postings with a fit
score and rationale), Coaching (skill-gap projects with review-first evidence submission -- a
passed evaluation can propose a resume update, never applied without explicit approval), Resume
(source content plus any pending proposals), Skills (claimed vs. proven capability, with a "Quick
skill check" mini-interview to test a claimed skill), Applications (pipeline by status), Prep
(behavioral interview questions and STAR stories built from real work history), and Settings
(preferences, job sources, LLM provider, updates). Nothing happens automatically in a way that
could surprise the user: resume changes are proposals requiring approval, project submission is
explicit, application status changes are explicit."""

MAX_JOBS_IN_CONTEXT = 15
MAX_PROJECTS_IN_CONTEXT = 10
MAX_HISTORY_MESSAGES = 20


def build_context(session: Session, profile: Profile) -> str:
    """A bounded plain-text summary of this profile's resume, top matched
    jobs, and active coaching projects -- handed to the LLM alongside every
    question so it never has to guess at the candidate's actual situation."""
    parts: list[str] = [HANARR_GUIDE_SUMMARY]

    resume_summary = json.loads(profile.resume_summary_json or "{}")
    if resume_summary:
        parts.append("RESUME SUMMARY: " + json.dumps(resume_summary))
    elif profile.resume_text:
        parts.append("RESUME TEXT (first 2000 chars): " + profile.resume_text[:2000])
    else:
        parts.append("RESUME: none uploaded yet.")

    scored_jobs = (
        session.query(JobPosting)
        .filter(JobPosting.profile_id == profile.id, JobPosting.fit_score.isnot(None))
        .order_by(JobPosting.fit_score.desc())
        .limit(MAX_JOBS_IN_CONTEXT)
        .all()
    )
    if scored_jobs:
        job_lines = [
            f"- #{j.id} \"{j.title}\" at {j.company} (status={j.status.value}, "
            f"fit_score={j.fit_score:.0f}): {(j.fit_rationale or '')[:300]}"
            for j in scored_jobs
        ]
        parts.append("TOP MATCHED JOBS (by fit score):\n" + "\n".join(job_lines))
    else:
        parts.append("MATCHED JOBS: none scored yet.")

    active_projects = (
        session.query(Project)
        .filter(
            Project.profile_id == profile.id,
            Project.status.in_([ProjectStatus.PLANNED, ProjectStatus.ACTIVE]),
        )
        .order_by(Project.id.desc())
        .limit(MAX_PROJECTS_IN_CONTEXT)
        .all()
    )
    if active_projects:
        project_lines = [
            f"- #{p.id} \"{p.title}\" (status={p.status.value}): {(p.target_outcome or '')[:200]}"
            for p in active_projects
        ]
        parts.append("ACTIVE COACHING PROJECTS:\n" + "\n".join(project_lines))
    else:
        parts.append("ACTIVE COACHING PROJECTS: none.")

    return "\n\n".join(parts)


def recent_messages(session: Session, profile_id: int, limit: int = MAX_HISTORY_MESSAGES) -> list[ChatMessage]:
    """Oldest-first, for both rendering the thread and building the
    conversation-history text handed to the LLM."""
    rows = (
        session.query(ChatMessage)
        .filter(ChatMessage.profile_id == profile_id)
        .order_by(ChatMessage.id.desc())
        .limit(limit)
        .all()
    )
    return list(reversed(rows))


def add_message(
    session: Session, profile_id: int, role: ChatRole, content: str, *, action_json: str | None = None,
) -> ChatMessage:
    message = ChatMessage(
        profile_id=profile_id, role=role, content=content,
        action_json=action_json, action_status="pending" if action_json else None,
    )
    session.add(message)
    session.flush()
    return message


class CoachError(RuntimeError):
    """The configured LLM couldn't answer -- there is no deterministic
    fallback for an open-ended chat the way scoring/extraction have one."""


def ask_coach(session: Session, profile: Profile, llm: LLMClient, question: str) -> str:
    """Answers one turn and persists both the question and the answer.
    Raises CoachError on any LLM failure -- callers should NOT persist a
    fabricated answer, unlike the deterministic-fallback pattern used
    elsewhere in this app, since there's nothing safe to fall back to for
    free-form advice."""
    history = recent_messages(session, profile.id)
    context = build_context(session, profile)

    transcript = "\n".join(
        f"{'Candidate' if m.role == ChatRole.USER else 'Coach'}: {m.content}" for m in history
    )
    user_prompt = (
        f"CONTEXT:\n{context}\n\n"
        + (f"CONVERSATION SO FAR:\n{transcript}\n\n" if transcript else "")
        + f"Candidate's new message: {question}"
    )

    add_message(session, profile.id, ChatRole.USER, question)

    try:
        raw = llm.complete_json(SYSTEM_PROMPT, user_prompt)
        data = json.loads(raw)
        answer = str(data.get("answer", "")).strip()
        if not answer:
            raise ValueError("empty answer")
    except Exception as exc:
        raise CoachError("Coach couldn't process that -- try again in a moment.") from exc

    add_message(session, profile.id, ChatRole.ASSISTANT, answer)
    return answer
