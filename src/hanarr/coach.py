"""Coach: an in-app chatbot grounded in the profile's own stored data
(resume, matched jobs, skill gaps, coaching projects) rather than a blank
general-purpose chatbot -- see DEVELOPMENT_LOG.md's phased plan.

Phase 1: a persisted conversation and read-only, data-grounded Q&A.
Phase 2 (this file too, now): action requests -- "create a project for
Kubernetes on the Acme job" -- resolved against coach_actions.py's fixed,
reviewed registry. An action is only ever a *proposal* until the user
explicitly confirms it from the chat UI (see dashboard/app.py's
/api/coach/messages/{id}/confirm); nothing in coach_actions.ACTIONS runs
from the LLM's output alone.

Deliberately a bounded full-context summary rather than real
retrieval/embeddings: Hanarr's actual scale (one person's job search,
dozens not millions of rows) means everything relevant fits comfortably in
a modern context window -- a vector index would be solving a scale problem
this app doesn't have.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from .coach_actions import action_catalog_text, run_action
from .llm.base import LLMClient
from .models import ChatMessage, ChatRole, JobPosting, Profile, Project, ProjectStatus
from .skill_analysis import profile_skill_page

SYSTEM_PROMPT = """You are Coach, a career-coaching assistant built into Hanarr, a local-first
job-search app. Answer only from the CONTEXT given in the user message and the conversation so
far -- never invent a fact about the candidate's resume, jobs, skills, or projects that isn't
actually present in CONTEXT. If something isn't in CONTEXT, say plainly that you don't have that
information rather than guessing. Be direct and specific: reference actual job titles, companies,
and skill/project names from CONTEXT instead of vague generalities. You can also explain how
Hanarr's own features work using the HANARR GUIDE section of CONTEXT.

You can also propose one of a fixed set of actions when the candidate clearly asks for one of
them (never propose an action for a plain question). Available actions:
{action_catalog}

To resolve an action's skill_id/job_id, match the candidate's wording against the IDs listed in
CONTEXT's TRACKED SKILLS / ALL SAVED JOBS sections -- never invent an id. If the candidate's
request is ambiguous (e.g. more than one skill or job plausibly matches, or none does), do not
guess: ask a clarifying question as a normal answer instead of proposing an action.

Return ONLY JSON, in exactly one of these two shapes:
{{"type": "answer", "answer": "..."}}
{{"type": "action", "action": "<action name>", "params": {{...}}, "summary": "one sentence describing exactly what this will do, for a confirmation prompt"}}"""

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
explicit, application status changes are explicit -- Coach follows the same rule: an action is
only ever proposed, never run, until the candidate explicitly confirms it."""

MAX_JOBS_IN_CONTEXT = 15
MAX_ALL_JOBS_IN_CONTEXT = 50
MAX_SKILLS_IN_CONTEXT = 50
MAX_PROJECTS_IN_CONTEXT = 10
MAX_HISTORY_MESSAGES = 20


def build_context(session: Session, profile: Profile) -> str:
    """A bounded plain-text summary of this profile's resume, matched
    jobs, tracked skills, and active coaching projects -- handed to the
    LLM alongside every question so it never has to guess at the
    candidate's actual situation, and so action params can be resolved to
    real ids instead of invented ones."""
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

    all_jobs = (
        session.query(JobPosting)
        .filter(JobPosting.profile_id == profile.id)
        .order_by(JobPosting.id.desc())
        .limit(MAX_ALL_JOBS_IN_CONTEXT)
        .all()
    )
    if all_jobs:
        all_job_lines = [f"- id={j.id}: \"{j.title}\" at {j.company} (status={j.status.value})" for j in all_jobs]
        parts.append("ALL SAVED JOBS (id, title, company, status -- for resolving which job a request means):\n" + "\n".join(all_job_lines))

    skills = profile_skill_page(session, profile)[:MAX_SKILLS_IN_CONTEXT]
    if skills:
        skill_lines = []
        for s in skills:
            gap_jobs = [j for j in s["jobs"] if j["status"] in ("missing", "partial")]
            gap_note = f", gap on {len(gap_jobs)} job(s)" if gap_jobs else ""
            skill_lines.append(f"- id={s['id']}: \"{s['name']}\"{gap_note}")
        parts.append("TRACKED SKILLS (id, name -- for resolving which skill a request means):\n" + "\n".join(skill_lines))
    else:
        parts.append("TRACKED SKILLS: none yet.")

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


def ask_coach(session: Session, profile: Profile, llm: LLMClient, question: str) -> dict[str, Any]:
    """Answers one turn and persists both the question and the response.
    Raises CoachError on any LLM failure or malformed output -- callers
    should NOT persist a fabricated answer, unlike the deterministic-
    fallback pattern used elsewhere in this app, since there's nothing
    safe to fall back to for free-form advice or an action proposal.

    Returns {"type": "answer", "answer": ..., "message_id": ...} or
    {"type": "action", "action": ..., "params": ..., "summary": ...,
    "message_id": ...} -- the action is NOT executed here; see
    confirm_action()."""
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
    system_prompt = SYSTEM_PROMPT.format(action_catalog=action_catalog_text())

    add_message(session, profile.id, ChatRole.USER, question)

    try:
        raw = llm.complete_json(system_prompt, user_prompt)
        data = json.loads(raw)
        kind = data.get("type")
        if kind == "action":
            action_name = str(data["action"])
            params = data.get("params") or {}
            summary = str(data.get("summary", "")).strip() or f"Run {action_name}?"
            if not isinstance(params, dict):
                raise ValueError("params must be an object")
        elif kind == "answer":
            answer = str(data.get("answer", "")).strip()
            if not answer:
                raise ValueError("empty answer")
        else:
            raise ValueError(f"unknown response type {kind!r}")
    except Exception as exc:
        raise CoachError("Coach couldn't process that -- try again in a moment.") from exc

    if kind == "action":
        action_json = json.dumps({"action": action_name, "params": params, "summary": summary})
        message = add_message(session, profile.id, ChatRole.ASSISTANT, summary, action_json=action_json)
        return {"type": "action", "action": action_name, "params": params, "summary": summary, "message_id": message.id}

    message = add_message(session, profile.id, ChatRole.ASSISTANT, answer)
    return {"type": "answer", "answer": answer, "message_id": message.id}


def confirm_action(session: Session, profile: Profile, llm: LLMClient, message_id: int) -> dict:
    """Runs a pending action proposal -- the one place coach_actions.run_action()
    is ever called from. Only reachable by an explicit user click (see
    dashboard/app.py); the LLM's own output never reaches here directly."""
    message = session.get(ChatMessage, message_id)
    if message is None or message.profile_id != profile.id:
        raise ValueError("Message not found.")
    if message.action_json is None:
        raise ValueError("This message has no pending action.")
    if message.action_status != "pending":
        raise ValueError(f"This action is already {message.action_status}.")

    proposal = json.loads(message.action_json)
    result = run_action(session, profile, llm, proposal["action"], proposal["params"])
    message.action_status = "confirmed"
    session.flush()
    return result


def decline_action(session: Session, profile: Profile, message_id: int) -> None:
    message = session.get(ChatMessage, message_id)
    if message is None or message.profile_id != profile.id:
        raise ValueError("Message not found.")
    if message.action_json is None:
        raise ValueError("This message has no pending action.")
    if message.action_status != "pending":
        raise ValueError(f"This action is already {message.action_status}.")
    message.action_status = "declined"
    session.flush()
