"""Project-pass resume proposals, approval, parsing, and affected-job rescoring."""
from __future__ import annotations

import difflib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .llm.base import LLMClient
from .matching import score_fit
from .models import (
    JobPosting,
    Profile,
    ProfileSkill,
    Project,
    ProjectEvaluation,
    ResumeProposal,
    ResumeProposalStatus,
    ResumeVersion,
    ScoreSnapshot,
    Skill,
    utc_now,
)
from .resume import extract_profile_summary
from .resume_sections import split_resume_into_sections
from .connectors.base import RawJobPosting

SYSTEM_PROMPT = """Write a conservative resume proposal based on a passed coaching project.
Return ONLY JSON: {"proposed_content":"...", "rationale":"..."}.
Preserve all existing truthful content, add only evidence supported by the project, and
never invent employers, dates, metrics, or skills."""


def _active_version(session: Session, profile: Profile) -> ResumeVersion:
    version = session.scalar(
        select(ResumeVersion).where(
            ResumeVersion.profile_id == profile.id, ResumeVersion.is_active.is_(True)
        ).order_by(ResumeVersion.id.desc())
    )
    if version is None:
        version = ResumeVersion(profile_id=profile.id, content=profile.resume_text or "", is_active=True)
        session.add(version)
        session.flush()
    return version


def _project_evidence(project: Project, evaluation: ProjectEvaluation) -> str:
    submission = evaluation.submission
    return " ".join(
        part for part in (project.title, project.target_outcome, submission.title, submission.content, evaluation.feedback)
        if part
    )


def create_resume_proposal(
    session: Session, profile_id: int, project: Project, evaluation: ProjectEvaluation, llm: LLMClient
) -> ResumeProposal:
    base = _active_version(session, project.profile)
    fallback_content = base.content
    evidence = _project_evidence(project, evaluation)
    if evidence and evidence.lower() not in fallback_content.lower():
        fallback_content = f"{fallback_content.rstrip()}\n\nProject evidence: {evidence}".strip()
    rationale = "Added only evidence from the passed coaching project; review before activation."
    source = "deterministic"
    try:
        raw = llm.complete_json(
            SYSTEM_PROMPT,
            json.dumps({"resume": base.content, "project": project.title, "evidence": evidence}),
        )
        value = json.loads(raw)
        proposed = str(value["proposed_content"]).strip()
        if not proposed:
            raise ValueError("empty resume proposal")
        fallback_content = proposed
        rationale = str(value.get("rationale") or rationale).strip()
        source = "llm"
    except Exception as exc:
        rationale = f"{rationale} Model proposal unavailable; deterministic fallback used ({type(exc).__name__})."
    proposal = ResumeProposal(
        profile_id=profile_id,
        base_version_id=base.id,
        project_id=project.id,
        proposed_content=fallback_content,
        diff="".join(difflib.unified_diff(
            base.content.splitlines(keepends=True), fallback_content.splitlines(keepends=True),
            fromfile="active-resume", tofile="proposal",
        )),
        rationale=rationale,
    )
    session.add(proposal)
    session.flush()
    return proposal


def _refresh_profile_skills(session: Session, profile: Profile, summary: dict[str, Any]) -> None:
    for name in summary.get("skills") or []:
        clean = str(name).strip()
        if not clean:
            continue
        slug = clean.lower().replace(" ", "-")
        skill = session.scalar(select(Skill).where(Skill.slug == slug))
        if skill is None:
            skill = Skill(name=clean, slug=slug)
            session.add(skill)
            session.flush()
        existing = session.scalar(
            select(ProfileSkill).where(ProfileSkill.profile_id == profile.id, ProfileSkill.skill_id == skill.id)
        )
        if existing is None:
            session.add(ProfileSkill(
                profile_id=profile.id, skill_id=skill.id, evidence="Extracted from approved resume",
                confidence=0.7, source="resume",
            ))


def approve_resume_proposal(
    session: Session, settings: Settings, profile_id: int, proposal_id: int, llm: LLMClient
) -> dict[str, Any]:
    proposal = session.get(ResumeProposal, proposal_id)
    if proposal is None or proposal.profile_id != profile_id:
        raise ValueError("Resume proposal not found.")
    if proposal.status is not ResumeProposalStatus.PENDING:
        raise ValueError("Only pending resume proposals can be approved.")
    profile = session.get(Profile, profile_id)
    assert profile is not None
    active = _active_version(session, profile)
    if proposal.base_version_id != active.id:
        raise ValueError("Resume proposal is stale; review a new proposal based on the current resume.")
    old_content = profile.resume_text or ""
    for version in profile.resume_versions:
        version.is_active = False
    version = ResumeVersion(
        profile_id=profile.id, content=proposal.proposed_content,
        metadata_json=json.dumps({"proposal_id": proposal.id, "project_id": proposal.project_id}),
        is_active=True,
    )
    session.add(version)
    proposal.status = ResumeProposalStatus.APPROVED
    proposal.decided_at = utc_now()
    profile.resume_text = proposal.proposed_content
    summary = extract_profile_summary(proposal.proposed_content, llm)
    profile.resume_summary_json = json.dumps(summary)
    _refresh_profile_skills(session, profile, summary)
    affected = []
    project = session.get(Project, proposal.project_id) if proposal.project_id else None
    jobs = [link.job for link in project.affected_jobs] if project else list(profile.jobs)
    prefs = settings.preferences
    for job in jobs:
        before = job.fit_score
        raw = RawJobPosting(
            source=job.source, external_id=job.external_id, company=job.company, title=job.title,
            location=job.location, remote=job.remote, url=job.url, description=job.description,
            salary_min=job.salary_min, salary_max=job.salary_max, posted_at=job.posted_at,
        )
        score, rationale = score_fit(raw, summary, proposal.proposed_content, prefs, llm)
        job.fit_score, job.fit_rationale = score, rationale
        session.add(ScoreSnapshot(
            profile_id=profile.id, job_id=job.id, fit_score=score, fit_rationale=rationale,
            trigger="resume_approved",
            scorer_metadata_json=json.dumps({
                "proposal_id": proposal.id, "before_score": before, "after_score": score,
                "explanation": "Rescored after approved resume re-parse.",
                "changed_resume_wording": old_content != proposal.proposed_content,
            }),
        ))
        affected.append({"job_id": job.id, "before_score": before, "after_score": score, "rationale": rationale})
    session.flush()
    return {"version_id": version.id, "proposal_id": proposal.id, "summary": summary, "affected_jobs": affected}


def reject_resume_proposal(session: Session, profile_id: int, proposal_id: int) -> ResumeProposal:
    proposal = session.get(ResumeProposal, proposal_id)
    if proposal is None or proposal.profile_id != profile_id:
        raise ValueError("Resume proposal not found.")
    if proposal.status is not ResumeProposalStatus.PENDING:
        raise ValueError("Only pending resume proposals can be rejected.")
    proposal.status = ResumeProposalStatus.REJECTED
    proposal.decided_at = utc_now()
    return proposal


def resume_status(profile: Profile, session: Session | None = None) -> dict[str, Any]:
    active = next((version for version in reversed(profile.resume_versions) if version.is_active), None)
    summary = {}
    if profile.resume_summary_json:
        try:
            summary = json.loads(profile.resume_summary_json)
        except json.JSONDecodeError:
            summary = {}
    impacts = []
    if session is not None:
        snapshots = session.scalars(
            select(ScoreSnapshot)
            .where(
                ScoreSnapshot.profile_id == profile.id,
                ScoreSnapshot.trigger == "resume_approved",
            )
            .order_by(ScoreSnapshot.created_at.desc(), ScoreSnapshot.id.desc())
        ).all()
        for snapshot in snapshots:
            job = session.get(JobPosting, snapshot.job_id)
            metadata = json.loads(snapshot.scorer_metadata_json or "{}")
            impacts.append({
                "job_id": snapshot.job_id,
                "title": job.title if job else "Saved job",
                "company": job.company if job else "",
                "before_score": metadata.get("before_score"),
                "after_score": metadata.get("after_score", snapshot.fit_score),
                "delta": (
                    metadata.get("after_score", snapshot.fit_score)
                    - metadata["before_score"]
                    if metadata.get("before_score") is not None
                    else None
                ),
                "created_at": snapshot.created_at.isoformat(),
                "explanation": metadata.get("explanation", snapshot.fit_rationale),
            })
    active_content = active.content if active else (profile.resume_text or "")
    return {
        "active": {
            "id": active.id if active else None,
            "label": active.label if active else None,
            "content": active_content,
            "sections": [{"heading": s.heading, "body": s.body} for s in split_resume_into_sections(active_content)],
        },
        "extracted_profile": summary,
        "matcher": {
            "status": "ready" if (active and active.content.strip()) else "not_ready",
            "source": "active resume",
            "version_id": active.id if active else None,
            "message": (
                "This approved resume is parsed and feeds new job matching and rescoring."
                if active and active.content.strip()
                else "Add or approve a resume before job matching can use it."
            ),
        },
        "proposals": [
            {"id": p.id, "status": p.status.value, "content": p.proposed_content, "diff": p.diff,
             "rationale": p.rationale, "project_id": p.project_id,
             "base_version_id": p.base_version_id, "created_at": p.created_at.isoformat()}
            for p in sorted(profile.resume_proposals, key=lambda item: item.id, reverse=True)
        ],
        "versions": [
            {"id": v.id, "active": v.is_active, "content": v.content, "label": v.label,
             "created_at": v.created_at.isoformat()}
            for v in sorted(profile.resume_versions, key=lambda item: item.id, reverse=True)
        ],
        "score_impacts": impacts,
    }
