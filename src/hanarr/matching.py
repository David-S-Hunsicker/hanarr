"""Two-stage matching:

1. `passes_prefilter` — cheap, deterministic rules (keywords, location,
   remote, salary floor) that cut volume before anything touches the LLM.
2. `score_fit` — LLM-based fit scoring against the resume + preferences,
   with a keyword-overlap fallback when llm.provider = 'none'.
"""
from __future__ import annotations

import json
import logging
import re

from .config import Preferences
from .connectors.base import RawJobPosting
from .llm.base import LLMClient

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are scoring how well a job posting fits a candidate, for a personal \
job-search tool. You will be given the candidate's full resume text, a structured summary of \
it, their stated preferences, and a job posting.

First, find the posting's minimum/required qualifications section (however it's labeled —
"Requirements", "You have", "Minimum qualifications", "What you'll need", etc.) and its
preferred/nice-to-have qualifications if present. Check each qualification — required AND \
preferred — against what the resume's WORK EXPERIENCE and PROJECT sections actually describe \
the candidate doing, not against a skills list or summary line by itself. A skill appearing \
only in a "Skills" list, or a technology mentioned only as something the candidate is \
"currently learning" / "completing a course in" / studying, is a claimed familiarity, not \
demonstrated experience — do not describe it as "extensive experience," "a strong \
background," or similar unless the work history or project bullets actually show hands-on \
use of it (built something with it, shipped it, applied it to a real problem). A candidate \
whose only connection to a preferred qualification is a skills-list keyword or in-progress \
coursework should be scored as PARTIALLY meeting that qualification at best, not as \
possessing it. Preferred/nice-to-have qualifications matter less than required ones: missing \
several of them should cost some points but not disqualify a strong-otherwise candidate — but \
don't claim the candidate meets a preferred qualification they haven't actually demonstrated.

Then return ONLY a JSON object:

{
  "score": <integer 0-100, how strong a fit this is>,
  "unmet_requirements": ["each REQUIRED qualification the resume does not clearly satisfy"],
  "fails_minimum_requirements": <true if the candidate is missing MULTIPLE required \
qualifications, or is missing ONE major one (e.g. years of experience far short of what's \
asked, a required credential/clearance/degree they don't hold, required hands-on experience \
with something core to the role that's absent from their history) — false if they're missing \
at most one minor required qualification or none at all>,
  "dealbreaker_hit": <true if the posting appears to violate ANY of the candidate's stated \
dealbreakers, false otherwise>,
  "rationale": "1-3 sentences explaining the score. Only mention what's actually relevant to \
THIS score — if a dealbreaker was hit or a requirement is unmet, say so specifically; if there \
were no dealbreakers or unmet requirements, don't mention dealbreakers/requirements at all, \
just explain the fit. Never state the absence of a problem (no 'no dealbreakers found', no \
'meets all requirements' filler) — only state what's actually noteworthy. Address the \
candidate directly as 'you' (e.g. 'You have strong Python experience but lack the required \
security clearance'), never as 'the candidate' or by name — this text is shown directly to \
them."
}

Scoring guide (before the fails_minimum_requirements override below): 80-100 only if the
candidate clearly meets essentially all required qualifications; 50-79 if they meet most
required qualifications but are missing one or two minor ones, or are missing several
preferred ones.

Both fails_minimum_requirements and the candidate's stated dealbreakers are hard
disqualifiers, not preferences to weigh in with everything else — if either applies, set the
corresponding field to true regardless of how well anything else matches; the caller will
zero out the score and disqualify the posting outright, so don't try to reflect that in the
"score" field yourself. Be honest and specific — this score is used to filter what the
candidate spends time reviewing, so don't inflate it, and don't guess a requirement is met
just because a related keyword appears somewhere in the posting."""


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


EMPLOYMENT_TYPES = ["full_time", "part_time", "contract", "internship"]


# A handful of countries commonly seen restricting "remote" roles on
# Greenhouse/Lever/RemoteOK-style boards, mapped to name variants that show
# up in a location string or "must be X-based" phrasing. This is not
# exhaustive — it only needs to catch the common case of a posting
# advertising remote work scoped to one specific other country, not every
# possible country in the world.
_OTHER_COUNTRY_NAMES = {
    "canada": ["canada", "canadian"],
    "united kingdom": ["united kingdom", "uk", "u.k.", "britain"],
    "germany": ["germany", "german"],
    "india": ["india"],
    "australia": ["australia", "australian"],
    "mexico": ["mexico", "mexican"],
    "brazil": ["brazil", "brazilian"],
    "philippines": ["philippines", "filipino"],
    "poland": ["poland", "polish"],
    "ireland": ["ireland", "irish"],
    "eu": ["european union", "eu member state"],
}

# Phrasing that signals an explicit restriction/requirement, checked against
# the description — deliberately narrow (see module docstring) to avoid
# false-rejecting a posting that only mentions a country in passing (e.g.
# "we have an office in Canada" without restricting this specific role).
_RESTRICTION_PATTERNS = [
    r"\bmust be (a )?{variant}[ -]based\b",
    r"\bmust be (a )?{variant} resident\b",
    r"\b{variant} residen(t|cy) (is )?required\b",
    r"\bopen (only )?to {variant}\b",
    r"\b{variant}[ -]based candidates? only\b",
    r"\b{variant} only\b",
    r"\bthis role is (based |located )?in {variant}\b",
]


def _detected_other_country_restriction(job: RawJobPosting, work_country: str) -> str | None:
    """Returns the name of another country the posting appears to restrict
    remote work to, or None if no such restriction is detected. The
    location field is checked by whole-word match (it's a short, structured
    field so a country name appearing there reliably means the role is
    scoped to it); the description is only checked against explicit
    restriction phrasing, not any mention of a country name, since a
    posting merely mentioning a country (e.g. an office location) doesn't
    mean this specific role is restricted to it."""
    if not work_country.strip():
        return None  # check disabled

    location_lower = job.location.lower()
    text_lower = f"{job.title} {job.description}".lower()

    for country, variants in _OTHER_COUNTRY_NAMES.items():
        if country == work_country.strip().lower():
            continue  # the candidate's own configured country, not a restriction
        for variant in variants:
            escaped = re.escape(variant)
            if re.search(rf"\b{escaped}\b", location_lower):
                return country
            for pattern in _RESTRICTION_PATTERNS:
                if re.search(pattern.format(variant=escaped), text_lower):
                    return country

    return None


def passes_prefilter(job: RawJobPosting, prefs: Preferences) -> bool:
    return prefilter_rejection_reason(job, prefs) is None


def prefilter_rejection_reason(job: RawJobPosting, prefs: Preferences) -> str | None:
    """Same rules as passes_prefilter, but returns the specific reason a
    posting was rejected instead of a bare bool -- feeds the dashboard's
    "why was this filtered out" debug view. None means it passes."""
    text = f"{job.title} {job.description}".lower()

    hit = next((kw for kw in prefs.keywords_exclude if kw.lower() in text), None)
    if hit:
        return f"Matched an excluded keyword: {hit!r}"

    if prefs.industries_exclude:
        hit = next((ind for ind in prefs.industries_exclude if ind.lower() in text), None)
        if hit:
            return f"Matched an excluded industry: {hit!r}"

    if job.remote and not prefs.remote_ok:
        return "Remote posting, but remote work isn't accepted in your preferences"
    if not job.remote and not prefs.onsite_ok and not prefs.willing_to_relocate:
        location_matches = any(
            loc.lower() in job.location.lower() for loc in prefs.locations if loc.lower() != "remote"
        )
        if not location_matches:
            return f"On-site posting in {job.location!r}, which isn't one of your accepted locations"

    # A "remote" posting is often remote *within one specific country* —
    # being remote-ok doesn't mean eligible for a "Remote - Canada" role.
    # This applies whether or not job.remote is set, since some sources
    # (Greenhouse) put the country restriction in the location field
    # without a clean remote/onsite flag either way.
    if _detected_other_country_restriction(job, prefs.work_country):
        return f"Restricted to a country other than {prefs.work_country!r}"

    if prefs.salary_floor_usd and job.salary_max:
        if job.salary_max < prefs.salary_floor_usd:
            return f"Max salary ${job.salary_max:,.0f} is below your ${prefs.salary_floor_usd:,.0f} floor"

    if prefs.employment_types:
        # Best-effort: only reject on an explicit contradiction in the title/description,
        # since most sources don't structure employment type cleanly.
        if "internship" not in [t.lower() for t in prefs.employment_types] and re.search(
            r"\bintern(ship)?\b", text
        ):
            return "Looks like an internship, which isn't one of your accepted employment types"

    candidate_levels = [s for s in prefs.seniority if s in SENIORITY_LEVELS]
    if candidate_levels:
        detected = _detected_title_seniority(job.title)
        if detected and detected in SENIORITY_LEVELS:
            # Reject only if the posting is too far from EVERY level the
            # candidate selected — someone open to both "senior" and
            # "staff" shouldn't lose a staff-adjacent posting just because
            # it's 2 rungs from "senior" alone.
            min_gap = min(
                abs(SENIORITY_LEVELS.index(detected) - SENIORITY_LEVELS.index(level))
                for level in candidate_levels
            )
            if min_gap > SENIORITY_TOLERANCE:
                return f"Detected seniority {detected!r} is too far from your selected levels"

    if prefs.target_titles or prefs.keywords_boost:
        # Nothing at all to anchor a match on — very unlikely to score well,
        # and not worth an LLM call to find out. Any single overlap (title
        # word or boost keyword, anywhere in the text) is enough to proceed;
        # this only screens out postings with literally no signal.
        title_words = {w for t in prefs.target_titles for w in t.lower().split() if len(w) > 2}
        has_title_overlap = any(w in job.title.lower() for w in title_words)
        has_boost_overlap = any(kw.lower() in text for kw in prefs.keywords_boost)
        if not has_title_overlap and not has_boost_overlap:
            return "No overlap with your target titles or boost keywords"

    return None


def score_fit(
    job: RawJobPosting,
    resume_summary: dict,
    resume_text: str,
    prefs: Preferences,
    llm: LLMClient,
) -> tuple[float, str]:
    """Returns (score 0-100, rationale). The LLM checks the posting's
    required qualifications against the candidate's actual resume text (not
    just the compressed skills/titles summary). Two things force the score
    to 0 outright rather than just lowering it: a hit on one of the
    candidate's stated dealbreakers, or failing minimum/required
    qualifications (missing multiple required items, or one major one) —
    see SYSTEM_PROMPT. Falls back to a keyword-overlap heuristic if the LLM
    call fails or isn't configured."""
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

        if data.get("fails_minimum_requirements"):
            fallback = "Disqualified: missing minimum/required qualifications."
            if unmet:
                fallback = "Disqualified: missing required qualification(s): " + "; ".join(unmet)
            return 0.0, rationale or fallback

        score = float(data.get("score", 0))
        if unmet and not rationale:
            rationale = "Unmet requirement(s): " + "; ".join(unmet)
        return max(0.0, min(100.0, score)), rationale
    except Exception as exc:  # noqa: BLE001 - fall back rather than block the pipeline
        # This used to be silent, which made every rule-based fallback
        # indistinguishable from llm.provider="none" -- there was no way to
        # tell "not configured" apart from "configured but every call is
        # failing" short of re-running score_fit by hand. Bounded so a huge
        # malformed-JSON dump doesn't flood the log.
        logger.warning(
            "LLM fit scoring failed for %r at %r, falling back to rule-based scoring: %s",
            job.title, job.company, str(exc)[:300],
        )
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
