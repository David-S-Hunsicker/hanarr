"""Requirement extraction and explainable skill-gap analysis.

This service is intentionally independent from fit scoring. It enriches saved
postings after they have been matched and never changes their score or status.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from .llm.base import LLMClient
from .models import (
    JobPosting,
    JobSkill,
    JobSkillRequirement,
    Profile,
    ProfileSkill,
    ProvenSkill,
    Skill,
    SkillGapStatus,
)

SYSTEM_PROMPT = """Extract concrete technical and professional skills required or preferred by
this job posting. Return ONLY JSON in this shape:
{"requirements": [{"name": "Python", "requirement": "required",
"evidence": "the exact short phrase from the posting", "confidence": 0.95}]}
Use requirement "required" for minimum qualifications and "preferred" for nice-to-haves.
Do not invent skills or include generic traits such as communication."""

# These terms make the no-LLM path useful on common postings while remaining
# conservative: a term is only stored when it occurs in the posting text.
COMMON_SKILLS = (
    "python", "java", "javascript", "typescript", "sql", "postgresql", "mysql",
    "aws", "azure", "gcp", "docker", "kubernetes", "terraform", "git",
    "linux", "react", "node.js", "go", "rust", "c++", "pandas", "spark",
    "machine learning", "data analysis", "rest", "graphql", "fastapi",
)


def skill_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _skill(session: Session, name: str) -> Skill:
    slug = skill_slug(name)
    skill = session.execute(select(Skill).where(Skill.slug == slug)).scalar_one_or_none()
    if skill is None:
        skill = Skill(name=name.strip(), slug=slug)
        session.add(skill)
        session.flush()
    return skill


def sync_profile_skills(session: Session, profile: Profile) -> list[Skill]:
    """Materialize resume-summary skills and retain manually/proven skills."""
    try:
        summary = json.loads(profile.resume_summary_json or "{}")
    except json.JSONDecodeError:
        summary = {}
    result = []
    for name in summary.get("skills") or []:
        if not str(name).strip():
            continue
        skill = _skill(session, str(name))
        result.append(skill)
        existing = session.execute(
            select(ProfileSkill).where(
                ProfileSkill.profile_id == profile.id, ProfileSkill.skill_id == skill.id
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                ProfileSkill(
                    profile_id=profile.id,
                    skill_id=skill.id,
                    source="resume",
                    confidence=0.7,
                    evidence="Extracted from the stored resume profile summary",
                )
            )
    session.flush()
    return result


def _fallback_requirements(description: str) -> list[dict]:
    text = description.lower()
    preferred = bool(re.search(r"\b(preferred|nice to have|bonus|plus)\b", text))
    found = []
    for name in COMMON_SKILLS:
        if re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", text):
            found.append(
                {
                    "name": name,
                    "requirement": "preferred" if preferred else "required",
                    "evidence": name,
                    "confidence": 0.55,
                }
            )
    return found


def _extract_requirements(job: JobPosting, llm: LLMClient) -> tuple[list[dict], str]:
    try:
        raw = llm.complete_json(
            SYSTEM_PROMPT,
            json.dumps({"title": job.title, "description": job.description[:12000]}),
        )
        data = json.loads(raw)
        requirements = data.get("requirements")
        if not isinstance(requirements, list):
            raise ValueError("LLM response requirements must be a list")
        return requirements, "llm"
    except Exception:
        return _fallback_requirements(f"{job.title}\n{job.description}"), "deterministic"


def analyze_job(session: Session, profile: Profile, job: JobPosting, llm: LLMClient) -> dict:
    """Extract, compare, and persist one posting's requirements and gaps."""
    profile_skills = sync_profile_skills(session, profile)
    profile_skill_rows = session.execute(
        select(ProfileSkill).where(ProfileSkill.profile_id == profile.id)
    ).scalars().all()
    profile_skill_strength = {
        row.skill.slug: (row.confidence, row.proficiency)
        for row in profile_skill_rows
    }
    proven_slugs = {
        row.skill.slug
        for row in session.execute(
            select(ProvenSkill).where(ProvenSkill.profile_id == profile.id)
        ).scalars()
    }

    requirements, source = _extract_requirements(job, llm)
    session.query(JobSkill).filter(JobSkill.job_id == job.id).delete(synchronize_session=False)
    records = []
    seen_slugs = set()
    for item in requirements:
        if not isinstance(item, dict) or not str(item.get("name", "")).strip():
            continue
        name = str(item["name"]).strip()
        skill = _skill(session, name)
        if skill.slug in seen_slugs:
            continue
        seen_slugs.add(skill.slug)
        requirement = (
            JobSkillRequirement.PREFERRED
            if str(item.get("requirement", "required")).lower() == "preferred"
            else JobSkillRequirement.REQUIRED
        )
        satisfied = skill.slug in proven_slugs or skill.slug in profile_skill_strength
        weak = (
            skill.slug in profile_skill_strength
            and (
                (profile_skill_strength[skill.slug][0] is not None
                 and profile_skill_strength[skill.slug][0] < 0.6)
                or (profile_skill_strength[skill.slug][1] is not None
                    and profile_skill_strength[skill.slug][1] < 0.6)
            )
            and skill.slug not in proven_slugs
        )
        status = (
            SkillGapStatus.SATISFIED
            if satisfied and not weak
            else SkillGapStatus.PARTIAL
            if weak
            else SkillGapStatus.MISSING
        )
        record = JobSkill(
            job_id=job.id,
            skill_id=skill.id,
            requirement=requirement,
            gap_status=status,
            evidence=str(item.get("evidence", "")),
            rationale=(
                "Matched proven-skill or strong resume evidence."
                if status is SkillGapStatus.SATISFIED
                else "Resume evidence is present but weak or uncertain."
                if status is SkillGapStatus.PARTIAL
                else "No matching resume or proven-skill evidence was found."
            ),
            confidence=float(item.get("confidence", 0.5)),
            analyzed_at=dt.datetime.utcnow(),
        )
        session.add(record)
        records.append(record)
    session.flush()
    return {
        "job_id": job.id,
        "source": source,
        "requirements": [_job_skill_dict(record) for record in records],
        "gap_counts": dict(Counter(record.gap_status.value for record in records)),
    }


def _job_skill_dict(record: JobSkill) -> dict:
    return {
        "skill": {"id": record.skill.id, "name": record.skill.name, "slug": record.skill.slug},
        "requirement": record.requirement.value,
        "status": record.gap_status.value,
        "evidence": record.evidence,
        "rationale": record.rationale,
        "confidence": record.confidence,
        "analyzed_at": record.analyzed_at.isoformat() if record.analyzed_at else None,
    }


def saved_job_gaps(session: Session, profile: Profile) -> list[dict]:
    """Return analyzed gaps grouped by saved job for dashboard cards."""
    jobs = session.execute(
        select(JobPosting).where(JobPosting.profile_id == profile.id).order_by(JobPosting.id)
    ).scalars()
    output = []
    for job in jobs:
        records = session.execute(
            select(JobSkill).where(JobSkill.job_id == job.id).order_by(JobSkill.id)
        ).scalars().all()
        if not records:
            continue
        output.append(
            {
                "job": {
                    "id": job.id,
                    "title": job.title,
                    "company": job.company,
                    "url": job.url,
                    "fit_score": job.fit_score,
                },
                "gaps": [_job_skill_dict(record) for record in records],
                "gap_counts": dict(Counter(record.gap_status.value for record in records)),
            }
        )
    return output
