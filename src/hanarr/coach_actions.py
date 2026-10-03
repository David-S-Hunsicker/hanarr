"""Coach's action registry (phase 2 of the segmented Coach plan -- see
DEVELOPMENT_LOG.md). A fixed, reviewed list of actions Coach can propose,
each a thin wrapper around an existing backend function -- never an
arbitrary write. The LLM's job in coach.py is narrowed to: resolve a
skill/job name from CONTEXT to a real id, pick which registered action
applies, and extract its parameters as structured JSON. Every action still
requires an explicit confirm click in the chat before run_action() is ever
called (see dashboard/app.py's /api/coach/messages/{id}/confirm) -- the
same explicit-click principle the rest of this app holds to everywhere
else (approving a resume proposal, cancelling a project).

Credentials/settings are deliberately never actions here -- those stay in
the dedicated masked-password Settings fields, not free-text chat.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy.orm import Session

from .coaching_projects import create_coaching_project, project_status
from .llm.base import LLMClient
from .models import ApplicationStatus, JobPosting, Profile, ProjectMode
from .skill_interview import interview_status, start_interview
from .star_stories import generate_star_questions, question_dict

ActionFn = Callable[[Session, Profile, LLMClient, dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class ActionSpec:
    name: str
    description: str  # shown to the LLM so it knows when/how to propose this action
    required_params: tuple[str, ...]
    execute: ActionFn


def _create_coaching_project(session: Session, profile: Profile, llm: LLMClient, params: dict[str, Any]) -> dict:
    skill_id = params.get("skill_id")
    job_id = params.get("job_id")
    mode = ProjectMode.POSTING_SPECIFIC if job_id is not None else ProjectMode.REUSABLE_SKILL
    return create_coaching_project(session, profile.id, mode, llm, job_id=job_id, skill_id=skill_id)


def _start_skill_interview(session: Session, profile: Profile, llm: LLMClient, params: dict[str, Any]) -> dict:
    interview = start_interview(session, profile.id, params["skill_id"], llm)
    return interview_status(interview)


def _generate_star_questions(session: Session, profile: Profile, llm: LLMClient, params: dict[str, Any]) -> dict:
    questions = generate_star_questions(session, profile, llm, job_id=params.get("job_id"))
    return {"questions": [question_dict(q, None) for q in questions]}


def _update_job_status(session: Session, profile: Profile, llm: LLMClient, params: dict[str, Any]) -> dict:
    job = session.get(JobPosting, params["job_id"])
    if job is None or job.profile_id != profile.id:
        raise ValueError("Saved job not found.")
    try:
        job.status = ApplicationStatus(params["status"])
    except ValueError:
        valid = ", ".join(s.value for s in ApplicationStatus)
        raise ValueError(f"status must be one of: {valid}.")
    return {"job_id": job.id, "status": job.status.value}


ACTIONS: dict[str, ActionSpec] = {
    "create_coaching_project": ActionSpec(
        name="create_coaching_project",
        description=(
            "Start a coaching project for a skill gap -- this designs a real project brief and "
            "task list, it's not a placeholder. Params: skill_id (required, int, from CONTEXT). "
            "job_id (optional, int, from CONTEXT) -- include it only when the project is for that "
            "specific saved job's gap; omit it for a general, reusable-skill project."
        ),
        required_params=("skill_id",),
        execute=_create_coaching_project,
    ),
    "start_skill_interview": ActionSpec(
        name="start_skill_interview",
        description=(
            "Start a Quick skill check (a short, bounded mini-interview) for a claimed skill. "
            "Params: skill_id (required, int, from CONTEXT)."
        ),
        required_params=("skill_id",),
        execute=_start_skill_interview,
    ),
    "generate_star_questions": ActionSpec(
        name="generate_star_questions",
        description=(
            "Generate behavioral interview practice questions. Params: job_id (optional, int, "
            "from CONTEXT) -- include it to weight questions toward that specific saved job's "
            "interview; omit for general practice questions."
        ),
        required_params=(),
        execute=_generate_star_questions,
    ),
    "update_job_status": ActionSpec(
        name="update_job_status",
        description=(
            "Change a saved job's pipeline status. Params: job_id (required, int, from CONTEXT), "
            "status (required, one of: new, reviewed, applied, interviewing, offer, rejected, "
            "dismissed)."
        ),
        required_params=("job_id", "status"),
        execute=_update_job_status,
    ),
}


def action_catalog_text() -> str:
    """A short description of every registered action, for the system
    prompt -- so the LLM knows what it's allowed to propose and with what
    parameters, without being handed the implementation itself."""
    return "\n".join(f"- {spec.name}: {spec.description}" for spec in ACTIONS.values())


def run_action(session: Session, profile: Profile, llm: LLMClient, action_name: str, params: dict[str, Any]) -> dict:
    spec = ACTIONS.get(action_name)
    if spec is None:
        raise ValueError(f"Unknown action {action_name!r}.")
    missing = [name for name in spec.required_params if params.get(name) is None]
    if missing:
        raise ValueError(f"Missing required parameter(s) for {action_name}: {', '.join(missing)}.")
    return spec.execute(session, profile, llm, params)
