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
job-search tool. You will be given the candidate's full resume text, a structured summary of \
it, their stated preferences, and a job posting.

First, find the posting's minimum/required qualifications section (however it's labeled —
"Requirements", "You have", "Minimum qualifications", "What you'll need", etc.) and its
preferred/nice-to-have qualifications if present. Check each REQUIRED qualification against
the candidate's actual resume text — not just vibes from the title or a skill keyword
appearing somewhere in the description. A required qualification that the resume does not
clearly satisfy (e.g. required years of experience the candidate doesn't have, a required
credential/clearance/degree they don't hold, required hands-on experience with something not
in their history) should sharply pull the score down, even if the role otherwise looks
appealing — required means required. Preferred/nice-to-have qualifications matter less: missing
several of them should cost some points but not disqualify a strong-otherwise candidate.

Then return ONLY a JSON object:

{
  "score": <integer 0-100, how strong a fit this is>,
  "unmet_requirements": ["each REQUIRED qualification the resume does not clearly satisfy"],
  "dealbreaker_hit": <true if the posting appears to violate ANY of the candidate's stated \
dealbreakers, false otherwise>,
  "rationale": "1-3 sentences explaining the score, calling out unmet requirements and any \
dealbreakers hit"
}

Scoring guide: 80-100 only if the candidate clearly meets essentially all required
qualifications; 50-79 if they meet most required qualifications but are missing one or two
minor ones, or are missing several preferred ones; below 50 if they're missing multiple
required qualifications or a major one (e.g. years of experience far short of what's asked).

The candidate's dealbreakers are hard requirements, not preferences to weigh in with
everything else — if the posting violates one, set dealbreaker_hit to true regardless of how
well anything else matches; the caller will disqualify the posting outright. Be honest and
specific — this score is used to filter what the candidate spends time reviewing, so don't
inflate it, and don't guess a requirement is met just because a related keyword appears
somewhere in the posting."""


# Ordered low to high. Used to reject postings whose title explicitly
# signals a seniority level far from the candidate's — e.g. an "Intern" or
# "Staff"/"Principal" title when the candidate is "senior". Each level's
# regex only matches unambiguous title language, not generic words that
# happen to overlap with normal job titles.
SENIORITY_LEVELS = ["intern", "junior", "mid", "senior", "staff", "principal", "exec"]
SENIORITY_TITLE_PATTERNS = {
    "intern": r"\bintern(ship)?\b",
    "junior": r"\bjunior\b|\bjr\.?\b|\bentry[- ]level\b|\bassociate\b",
    "staff": r"\bstaff\b",
    "principal": r"\bprincipal\b|\bdistinguished\b",
    "exec": r"\b(vp|vice president|chief|cto|ceo|coo|svp|evp|head of)\b",
}
# How many rungs of mismatch to tolerate before rejecting. One rung (e.g.
# senior candidate seeing a staff or mid posting) is normal market noise and
# often still worth the candidate's attention; two or more (e.g. senior vs.
# intern, or senior vs. exec) is not.
SENIORITY_TOLERANCE = 1


def _detected_title_seniority(title: str) -> str | None:
    title_lower = title.lower()
    for level, pattern in SENIORITY_TITLE_PATTERNS.items():
        if re.search(pattern, title_lower):
            return level
    return None


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

    if prefs.seniority in SENIORITY_LEVELS:
        detected = _detected_title_seniority(job.title)
        if detected and detected in SENIORITY_LEVELS:
            gap = abs(SENIORITY_LEVELS.index(detected) - SENIORITY_LEVELS.index(prefs.seniority))
            if gap > SENIORITY_TOLERANCE:
                return False

    if prefs.target_titles or prefs.keywords_boost:
        # Nothing at all to anchor a match on — very unlikely to score well,
        # and not worth an LLM call to find out. Any single overlap (title
        # word or boost keyword, anywhere in the text) is enough to proceed;
        # this only screens out postings with literally no signal.
        title_words = {w for t in prefs.target_titles for w in t.lower().split() if len(w) > 2}
        has_title_overlap = any(w in job.title.lower() for w in title_words)
        has_boost_overlap = any(kw.lower() in text for kw in prefs.keywords_boost)
        if not has_title_overlap and not has_boost_overlap:
            return False

    return True


def score_fit(
    job: RawJobPosting,
    resume_summary: dict,
    resume_text: str,
    prefs: Preferences,
    llm: LLMClient,
) -> tuple[float, str]:
    """Returns (score 0-100, rationale). The LLM checks the posting's
    required qualifications against the candidate's actual resume text (not
    just the compressed skills/titles summary), and a missing required
    qualification should sharply reduce the score — see SYSTEM_PROMPT. A hit
    on one of the candidate's stated dealbreakers forces the score to 0,
    since dealbreakers are hard requirements rather than one more factor
    blended into the score. Falls back to a keyword-overlap heuristic if the
    LLM call fails or isn't configured."""
    try:
        user_prompt = json.dumps(
            {
                "resume_text": resume_text[:8000],
                "resume_summary": resume_summary,
                "preferences": prefs.model_dump(),
                "job": {
                    "title": job.title,
                    "company": job.company,
                    "location": job.location,
                    "remote": job.remote,
                    "description": job.description[:8000],
                },
            }
        )
        raw = llm.complete_json(SYSTEM_PROMPT, user_prompt)
        data = json.loads(raw)
        rationale = data.get("rationale", "")
        unmet = data.get("unmet_requirements") or []

        if data.get("dealbreaker_hit"):
            return 0.0, rationale or "Disqualified: posting appears to violate a stated dealbreaker."

        score = float(data.get("score", 0))
        if unmet and not rationale:
            rationale = "Unmet requirement(s): " + "; ".join(unmet)
        return max(0.0, min(100.0, score)), rationale
    except Exception:  # noqa: BLE001 - fall back rather than block the pipeline
        return _rule_based_score(job, resume_summary, prefs)


def _rule_based_score(
    job: RawJobPosting, resume_summary: dict, prefs: Preferences
) -> tuple[float, str]:
    text = f"{job.title} {job.description}".lower()
    skills = [s.lower() for s in resume_summary.get("skills", [])]
    titles = [t.lower() for t in prefs.target_titles]
    boosts = [k.lower() for k in prefs.keywords_boost]

    # No LLM available to reason about phrasing, so this is a blunt substring
    # check — it will miss dealbreakers worded differently in the posting
    # than in the user's config, but it's the best a keyword fallback can do.
    hit = next((d for d in prefs.dealbreakers if d.lower() in text), None)
    if hit:
        return 0.0, f"Disqualified: posting text matches stated dealbreaker \"{hit}\"."

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
