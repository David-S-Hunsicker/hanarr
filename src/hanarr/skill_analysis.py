"""Requirement extraction and explainable skill-gap analysis.

This service is intentionally independent from fit scoring. It enriches saved
postings after they have been matched and never changes their score or status.
"""
from __future__ import annotations

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
    Project,
    ProjectSkill,
    Skill,
    SkillGapStatus,
    utc_now,
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
            analyzed_at=utc_now(),
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


def saved_job_gap(session: Session, profile: Profile, job_id: int) -> dict | None:
    """Return one analyzed saved posting, or None when it has no analysis."""
    for item in saved_job_gaps(session, profile):
        if item["job"]["id"] == job_id:
            return item
    return None


def profile_skill_page(session: Session, profile: Profile) -> list[dict]:
    """Build the evidence view without treating profile claims as proven."""
    sync_profile_skills(session, profile)
    rows = session.execute(
        select(ProfileSkill, Skill)
        .join(Skill, Skill.id == ProfileSkill.skill_id)
        .where(ProfileSkill.profile_id == profile.id)
        .order_by(Skill.name)
    ).all()
    capability_by_skill = {skill.id: row for row, skill in rows}
    proven = {
        item.skill_id: item
        for item in session.execute(
            select(ProvenSkill).where(ProvenSkill.profile_id == profile.id)
        ).scalars()
    }
    projects = session.execute(
        select(ProjectSkill, Project)
        .join(Project, Project.id == ProjectSkill.project_id)
        .where(Project.profile_id == profile.id)
    ).all()
    projects_by_skill: dict[int, list[dict]] = {}
    for project_skill, project in projects:
        projects_by_skill.setdefault(project_skill.skill_id, []).append(
            {
                "id": project.id,
                "title": project.title,
                "status": project.status.value,
                "target_level": project_skill.target_level,
                "evidence": project_skill.evidence,
            }
        )
    jobs = session.execute(
        select(JobSkill, JobPosting)
        .join(JobPosting, JobPosting.id == JobSkill.job_id)
        .where(JobPosting.profile_id == profile.id)
    ).all()
    jobs_by_skill: dict[int, list[dict]] = {}
    for job_skill, job in jobs:
        jobs_by_skill.setdefault(job_skill.skill_id, []).append(
            {
                "id": job.id,
                "title": job.title,
                "company": job.company,
                "url": job.url,
                "requirement": job_skill.requirement.value,
                "status": job_skill.gap_status.value,
                "evidence": job_skill.evidence,
                "confidence": job_skill.confidence,
            }
        )
        if job_skill.skill_id not in capability_by_skill:
            capability_by_skill[job_skill.skill_id] = None
    skills_by_id = {
        skill.id: skill
        for skill in session.execute(select(Skill).where(Skill.id.in_(set(jobs_by_skill) | set(capability_by_skill)))).scalars()
    }
    demand = {item["skill_id"]: item for item in market_demand_summary(session, profile)}
    return [
        {
            "id": skill.id,
            "name": skill.name,
            "slug": skill.slug,
            "capability": {
                "proficiency": row.proficiency if row else None,
                "confidence": row.confidence if row else None,
                "evidence": row.evidence if row else "",
                "source": row.source if row else "not recorded",
            },
            "proven": (
                {
                    "evidence": proven[skill.id].evidence,
                    "project_id": proven[skill.id].project_id,
                    "proven_at": proven[skill.id].proven_at.isoformat(),
                }
                if skill.id in proven
                else None
            ),
            "resume_wording": row is not None and row.source == "resume",
            "projects": projects_by_skill.get(skill.id, []),
            "jobs": jobs_by_skill.get(skill.id, []),
            "market_demand": demand.get(skill.id),
        }
        for skill_id, row in capability_by_skill.items()
        for skill in [skills_by_id[skill_id]]
    ]


def market_demand_summary(session: Session, profile: Profile) -> list[dict]:
    """Aggregate analyzed saved-job demand into reusable skill priorities."""
    rows = session.execute(
        select(JobSkill, JobPosting)
        .join(JobPosting, JobPosting.id == JobSkill.job_id)
        .where(JobPosting.profile_id == profile.id)
        .order_by(JobSkill.skill_id, JobSkill.id)
    ).all()
    profile_rows = {
        row.skill_id: row
        for row in session.execute(
            select(ProfileSkill).where(ProfileSkill.profile_id == profile.id)
        ).scalars()
    }
    grouped: dict[int, dict] = {}
    for job_skill, job in rows:
        item = grouped.setdefault(job_skill.skill_id, {
            "skill_id": job_skill.skill_id,
            "skill": {"id": job_skill.skill.id, "name": job_skill.skill.name, "slug": job_skill.skill.slug},
            "affected_job_ids": set(),
            "required_count": 0,
            "preferred_count": 0,
            "missing_count": 0,
            "partial_count": 0,
            "satisfied_count": 0,
            "jobs": [],
        })
        item["affected_job_ids"].add(job.id)
        if job_skill.requirement is JobSkillRequirement.REQUIRED:
            item["required_count"] += 1
        else:
            item["preferred_count"] += 1
        if job_skill.gap_status is SkillGapStatus.MISSING:
            item["missing_count"] += 1
        elif job_skill.gap_status is SkillGapStatus.PARTIAL:
            item["partial_count"] += 1
        elif job_skill.gap_status is SkillGapStatus.SATISFIED:
            item["satisfied_count"] += 1
        item["jobs"].append({
            "id": job.id,
            "title": job.title,
            "company": job.company,
            "requirement": job_skill.requirement.value,
            "status": job_skill.gap_status.value,
        })

    output = []
    for item in grouped.values():
        gap_weight = item["missing_count"] * 2 + item["partial_count"]
        demand_weight = item["required_count"] * 2 + item["preferred_count"]
        priority_score = demand_weight + gap_weight + len(item["affected_job_ids"])
        effort_points = 1 + item["missing_count"] * 2 + item["partial_count"]
        effort = "low" if effort_points <= 2 else "medium" if effort_points <= 5 else "high"
        correction = profile_rows.get(item["skill_id"])
        output.append({
            "skill_id": item["skill_id"],
            "skill": item["skill"],
            "affected_job_ids": sorted(item["affected_job_ids"]),
            "affected_job_count": len(item["affected_job_ids"]),
            "required_count": item["required_count"],
            "preferred_count": item["preferred_count"],
            "missing_count": item["missing_count"],
            "partial_count": item["partial_count"],
            "satisfied_count": item["satisfied_count"],
            "gap_count": item["missing_count"] + item["partial_count"],
            "priority_score": priority_score,
            "estimated_effort": effort,
            "effort_points": effort_points,
            "user_correction": (
                {
                    "proficiency": correction.proficiency,
                    "confidence": correction.confidence,
                    "evidence": correction.evidence,
                    "source": correction.source,
                }
                if correction is not None and correction.source == "manual"
                else None
            ),
            "jobs": item["jobs"],
        })
    return sorted(output, key=lambda item: (-item["priority_score"], item["skill"]["name"].lower()))


def coaching_suggestions(session: Session, profile: Profile) -> list[dict]:
    """Return one reusable suggestion per skill, ranked by market priority."""
    return [
        {
            **item,
            "status": "missing" if item["missing_count"] else "partial",
        }
        for item in market_demand_summary(session, profile)
        if item["gap_count"]
    ]
