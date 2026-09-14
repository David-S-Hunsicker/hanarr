"""Two-stage matching:

1. `passes_prefilter` — cheap, deterministic rules (keywords, location,
   remote, salary floor) that cut volume before anything touches the LLM.
2. `score_fit` — LLM-based fit scoring against the resume + preferences,
   with a keyword-overlap fallback when llm.provider = 'none'.
"""
from __future__ import annotations

import json
import re

from .config import Preferences
from .connectors.base import RawJobPosting
from .llm.base import LLMClient

SYSTEM_PROMPT = """You are scoring how well a job posting fits a candidate, for a personal \
job-search tool. Given the candidate's resume summary, their stated preferences, and a job \
posting, return ONLY a JSON object:

{
  "score": <integer 0-100, how strong a fit this is>,
  "rationale": "1-3 sentences explaining the score, mentioning any dealbreakers hit"
}

Weigh: title/skill overlap, seniority match, location/remote fit, and whether any of the \
candidate's stated dealbreakers appear to be violated. Be honest and specific — this score \
is used to filter what the candidate spends time reviewing, so don't inflate it."""


def passes_prefilter(job: RawJobPosting, prefs: Preferences) -> bool:
    text = f"{job.title} {job.description}".lower()

    if any(kw.lower() in text for kw in prefs.keywords_exclude):
        return False

    if prefs.industries_exclude and any(ind.lower() in text for ind in prefs.industries_exclude):
        return False

    if job.remote and not prefs.remote_ok:
        return False
    if not job.remote and not prefs.onsite_ok and not prefs.willing_to_relocate:
        location_matches = any(
            loc.lower() in job.location.lower() for loc in prefs.locations if loc.lower() != "remote"
        )
        if not location_matches:
            return False

    if prefs.salary_floor_usd and job.salary_max:
        if job.salary_max < prefs.salary_floor_usd:
            return False

    if prefs.employment_types:
        # Best-effort: only reject on an explicit contradiction in the title/description,
        # since most sources don't structure employment type cleanly.
        if "internship" not in [t.lower() for t in prefs.employment_types] and re.search(
            r"\bintern(ship)?\b", text
        ):
            return False

    return True


def score_fit(
    job: RawJobPosting,
    resume_summary: dict,
    prefs: Preferences,
    llm: LLMClient,
) -> tuple[float, str]:
    """Returns (score 0-100, rationale). Falls back to a keyword-overlap
    heuristic if the LLM call fails or isn't configured."""
    try:
        user_prompt = json.dumps(
            {
                "resume_summary": resume_summary,
                "preferences": prefs.model_dump(),
                "job": {
                    "title": job.title,
                    "company": job.company,
                    "location": job.location,
                    "remote": job.remote,
                    "description": job.description[:4000],
                },
            }
        )
        raw = llm.complete_json(SYSTEM_PROMPT, user_prompt)
        data = json.loads(raw)
        score = float(data.get("score", 0))
        return max(0.0, min(100.0, score)), data.get("rationale", "")
    except Exception:  # noqa: BLE001 - fall back rather than block the pipeline
        return _rule_based_score(job, resume_summary, prefs)


def _rule_based_score(
    job: RawJobPosting, resume_summary: dict, prefs: Preferences
) -> tuple[float, str]:
    text = f"{job.title} {job.description}".lower()
    skills = [s.lower() for s in resume_summary.get("skills", [])]
    titles = [t.lower() for t in prefs.target_titles]
    boosts = [k.lower() for k in prefs.keywords_boost]

    score = 40.0  # baseline once it's survived the prefilter
    if any(t in job.title.lower() for t in titles):
        score += 25
    skill_hits = sum(1 for s in skills if s in text)
    score += min(20, skill_hits * 4)
    boost_hits = sum(1 for b in boosts if b in text)
    score += min(15, boost_hits * 5)

    score = max(0.0, min(100.0, score))
    return score, (
        "Rule-based fallback score (no LLM configured or LLM call failed): "
        f"{skill_hits} skill keyword(s) and {boost_hits} boost keyword(s) matched."
    )
