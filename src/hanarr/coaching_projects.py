"""Manually opted-in coaching project creation from analyzed skill gaps."""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from .llm.base import LLMClient
from .models import (
    JobPosting,
    JobSkill,
    Project,
    ProjectJob,
    ProjectMode,
    ProjectSkill,
    ProjectTask,
    Skill,
    SkillGapStatus,
)
from .submissions import submission_status

SYSTEM_PROMPT = """Create a concise hands-on coaching project for closing a skill gap.
Return ONLY JSON in this shape:
{"title":"...", "description":"...", "target_outcome":"...",
"tasks":[{"title":"...", "description":"..."}]}
Provide 3 to 5 concrete tasks. Do not propose resume writing, job applications,
or submission evaluation."""


def _fallback_brief(title: str, skills: list[Skill], job_title: str | None) -> dict:
    names = ", ".join(skill.name for skill in skills)
    context = f" for the {job_title} role" if job_title else ""
    return {
        "title": f"{names} practical project",
        "description": f"Build a small, demonstrable project{context} that applies {names}.",
        "target_outcome": f"Produce a working artifact and evidence of applied {names}.",
        "tasks": [
            {"title": "Define the project", "description": f"Choose a focused problem and success measure for {names}."},
            {"title": "Build a first version", "description": f"Implement the core workflow using {names}."},
            {"title": "Validate and document", "description": "Test the result, capture evidence, and document tradeoffs."},
        ],
    }


def _brief(title: str, skills: list[Skill], job_title: str | None, llm: LLMClient) -> tuple[dict, str]:
    fallback = _fallback_brief(title, skills, job_title)
    try:
        raw = llm.complete_json(
            SYSTEM_PROMPT,
            json.dumps({"target": title, "skills": [skill.name for skill in skills], "job_title": job_title}),
        )
        result = json.loads(raw)
        tasks = result.get("tasks")
        if not isinstance(result, dict) or not isinstance(tasks, list) or not 3 <= len(tasks) <= 5:
            raise ValueError("invalid coaching brief")
        normalized = {
            "title": str(result.get("title") or fallback["title"]).strip(),
            "description": str(result.get("description") or fallback["description"]).strip(),
            "target_outcome": str(result.get("target_outcome") or fallback["target_outcome"]).strip(),
            "tasks": [
                {"title": str(task.get("title", "")).strip(), "description": str(task.get("description", "")).strip()}
                for task in tasks if isinstance(task, dict) and str(task.get("title", "")).strip()
            ],
        }
        if len(normalized["tasks"]) < 3:
            raise ValueError("brief tasks are incomplete")
        return normalized, "llm"
    except (ValueError, TypeError, json.JSONDecodeError, KeyError):
        return fallback, "deterministic"
    except Exception:
        return fallback, "deterministic"


def create_coaching_project(
    session: Session,
    profile_id: int,
    mode: ProjectMode,
    llm: LLMClient,
    *,
    job_id: int | None = None,
    skill_id: int | None = None,
) -> dict:
    if mode is ProjectMode.POSTING_SPECIFIC and job_id is None:
        raise ValueError("posting_specific projects require job_id")
    if mode is ProjectMode.REUSABLE_SKILL and skill_id is None:
        raise ValueError("reusable_skill projects require skill_id")

    jobs: list[JobPosting] = []
    if job_id is not None:
        job = session.get(JobPosting, job_id)
        if job is None or job.profile_id != profile_id:
            raise ValueError("saved job not found")
        jobs = [job]

    if mode is ProjectMode.POSTING_SPECIFIC:
        rows = session.execute(select(JobSkill).where(JobSkill.job_id == job_id)).scalars().all()
        rows = [row for row in rows if row.gap_status in (SkillGapStatus.MISSING, SkillGapStatus.PARTIAL)]
        if skill_id is not None:
            rows = [row for row in rows if row.skill_id == skill_id]
        if not rows:
            raise ValueError("job has no selected analyzed skill gap")
    else:
        skill = session.get(Skill, skill_id)
        if skill is None:
            raise ValueError("skill not found")
        rows = session.execute(
            select(JobSkill, JobPosting)
            .join(JobPosting, JobPosting.id == JobSkill.job_id)
            .where(JobPosting.profile_id == profile_id, JobSkill.skill_id == skill_id)
        ).all()
        rows = [row[0] for row in rows if row[0].gap_status in (SkillGapStatus.MISSING, SkillGapStatus.PARTIAL)]
        jobs = [session.get(JobPosting, row.job_id) for row in rows]

    skills = []
    for row in rows:
        if row.skill not in skills:
            skills.append(row.skill)
    job_title = jobs[0].title if len(jobs) == 1 else None
    brief, source = _brief(
        f"{' and '.join(skill.name for skill in skills)} coaching project",
        skills,
        job_title,
        llm,
    )
    project = Project(
        profile_id=profile_id,
        job_id=job_id if mode is ProjectMode.POSTING_SPECIFIC else None,
        title=brief["title"],
        mode=mode,
        description=brief["description"],
        target_outcome=brief["target_outcome"],
        brief_json=json.dumps({"source": source, **brief}),
    )
    session.add(project)
    session.flush()
    for skill in skills:
        project.skills.append(ProjectSkill(skill_id=skill.id))
    for job in {job.id: job for job in jobs if job is not None}.values():
        project.affected_jobs.append(ProjectJob(job_id=job.id))
    for position, task in enumerate(brief["tasks"], start=1):
        project.tasks.append(ProjectTask(position=position, title=task["title"], description=task["description"]))
    session.flush()
    return project_status(project)


def project_status(project: Project) -> dict:
    return {
        "id": project.id,
        "title": project.title,
        "mode": project.mode.value,
        "status": project.status.value,
        "description": project.description,
        "target_outcome": project.target_outcome,
        "brief": json.loads(project.brief_json or "{}"),
        "skills": [{"id": item.skill.id, "name": item.skill.name, "slug": item.skill.slug} for item in project.skills],
        "affected_job_ids": [item.job_id for item in project.affected_jobs],
        "tasks": [
            {"id": task.id, "position": task.position, "title": task.title, "description": task.description, "status": task.status.value}
            for task in sorted(project.tasks, key=lambda item: item.position)
        ],
        "submissions": [
            submission_status(submission)
            for submission in sorted(project.submissions, key=lambda item: item.id, reverse=True)
        ],
    }
