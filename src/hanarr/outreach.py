"""Networking outreach email drafting for a saved job posting -- the
"hidden job market" angle instead of more postings.

Deliberately out of scope: finding the *right person* to contact. LinkedIn
people search and contact-finder services (Hunter.io, Apollo.io, etc.)
either violate LinkedIn's ToS the same way automated Easy Apply would, or
are themselves built on scraped data of the same provenance -- neither fits
this app's no-scraping stance. The user supplies the contact (a name,
email, or LinkedIn URL they already found through their own network or a
company's team page); Hanarr only drafts the message, the same
"LLM drafts, you send it yourself" shape the cover letter already uses --
never a submission artifact, no approval step, regenerating overwrites the
stored draft.
"""
from __future__ import annotations

import json

from .llm.base import LLMClient
from .models import JobPosting, Profile, utc_now

SYSTEM_PROMPT = """Write a short, specific cold outreach / informational-interview email (under \
150 words) from the candidate to a contact at the hiring company, using only what's actually in \
the candidate's resume text. Return ONLY JSON: {"email": "..."}. Reference the company and role \
by name, connect 1-2 concrete points from the resume to the role, ask for a brief conversation \
(not directly for the job), and close politely. Never invent employers, dates, metrics, or skills \
the resume doesn't support, and never invent anything about the contact beyond their name."""


def _deterministic_outreach_email(profile: Profile, job: JobPosting, contact: str) -> str:
    """Used when no LLM is configured or the model call/response is
    unusable. A plain, honest mail-merge template beats returning nothing,
    and never fabricates content the resume doesn't already contain."""
    opening_skills = []
    try:
        summary = json.loads(profile.resume_summary_json or "{}")
        opening_skills = [str(s) for s in (summary.get("skills") or [])[:2]]
    except (json.JSONDecodeError, TypeError):
        pass
    skills_line = (
        f"my background in {' and '.join(opening_skills)}"
        if opening_skills
        else "my background"
    )
    contact_name = contact.split("@")[0].split("/")[-1].strip() or "there"
    return (
        f"Hi {contact_name},\n\n"
        f"I'm exploring the {job.title} role at {job.company} and thought I'd reach out "
        f"directly. Given {skills_line}, I think there could be a good fit, and I'd love to "
        f"hear more about the team and what you're looking for.\n\n"
        f"Would you have 15 minutes sometime in the next week or two for a quick call?\n\n"
        f"Thanks for considering it."
    )


def draft_outreach_email(profile: Profile, job: JobPosting, contact: str, llm: LLMClient) -> tuple[str, str]:
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
                "contact": contact,
            }),
        )
        content = str(json.loads(raw)["email"]).strip()
        if not content:
            raise ValueError("empty email")
        return content, "llm"
    except Exception:
        return _deterministic_outreach_email(profile, job, contact), "deterministic"


def generate_and_store_outreach_email(profile: Profile, job: JobPosting, contact: str, llm: LLMClient) -> JobPosting:
    content, source = draft_outreach_email(profile, job, contact, llm)
    job.outreach_contact = contact
    job.outreach_email = content
    job.outreach_email_source = source
    job.outreach_email_generated_at = utc_now()
    return job
