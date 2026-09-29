"""Cover-letter drafting for a saved job posting.

Deliberately simple compared to the resume-proposal loop: a cover letter
never "activates" anything and isn't reviewed/approved before use, so it's
just generated text the user edits and copies themselves -- there's no
submission or evaluation step. Regenerating overwrites the stored draft.
"""
from __future__ import annotations

import json

from .llm.base import LLMClient
from .models import JobPosting, Profile, utc_now

SYSTEM_PROMPT = """Write a concise, specific cover letter (3-4 short paragraphs) for the given \
job posting, using only what's actually in the candidate's resume text. Return ONLY JSON: \
{"cover_letter": "..."}. Reference the company and role by name, connect 2-3 concrete points \
from the resume to what the posting is asking for, and close with a brief, confident call to \
action. Never invent employers, dates, metrics, or skills the resume doesn't support."""


def _deterministic_cover_letter(profile: Profile, job: JobPosting) -> str:
    """Used when no LLM is configured or the model call/response is
    unusable. A plain, honest mail-merge template beats returning nothing,
    and never fabricates content the resume doesn't already contain."""
    opening_skills = []
    try:
        summary = json.loads(profile.resume_summary_json or "{}")
        opening_skills = [str(s) for s in (summary.get("skills") or [])[:3]]
    except (json.JSONDecodeError, TypeError):
        pass
    skills_line = (
        f"My background includes {', '.join(opening_skills)}, which lines up directly with "
        f"what this role is looking for."
        if opening_skills
        else "My resume outlines the experience I'd bring to this role."
    )
    return (
        f"Dear {job.company} team,\n\n"
        f"I'm writing to express my interest in the {job.title} position. {skills_line}\n\n"
        f"I've attached my resume with more detail on my background. I'd welcome the chance to "
        f"discuss how I could contribute to {job.company}.\n\n"
        f"Thank you for your consideration."
    )


def draft_cover_letter(profile: Profile, job: JobPosting, llm: LLMClient) -> tuple[str, str]:
    """Returns (content, source) where source is "llm" or "deterministic".
    Never raises -- an LLM failure falls back to the deterministic template
    rather than leaving the user with nothing."""
    try:
        raw = llm.complete_json(
            SYSTEM_PROMPT,
            json.dumps({
                "resume": (profile.resume_text or "")[:12000],
                "company": job.company,
                "title": job.title,
                "job_description": (job.description or "")[:6000],
            }),
        )
        content = str(json.loads(raw)["cover_letter"]).strip()
        if not content:
            raise ValueError("empty cover letter")
        return content, "llm"
    except Exception:
        return _deterministic_cover_letter(profile, job), "deterministic"


def generate_and_store_cover_letter(profile: Profile, job: JobPosting, llm: LLMClient) -> JobPosting:
    content, source = draft_cover_letter(profile, job, llm)
    job.cover_letter = content
    job.cover_letter_source = source
    job.cover_letter_generated_at = utc_now()
    return job
