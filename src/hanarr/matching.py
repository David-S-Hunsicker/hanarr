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
from typing import TYPE_CHECKING

from .config import Preferences

if TYPE_CHECKING:
    # Deferred: connectors/registry.py -> company_categories.py imports
    # EXAMPLE_TARGET_TITLES from this module, so a real (non-TYPE_CHECKING)
    # import here -- `from .connectors.base import RawJobPosting` pulls in
    # connectors/__init__.py, which pulls in registry.py, which pulls in
    # company_categories.py, which tries to import back from this
    # not-yet-finished module -- is a circular import that only fails
    # depending on which module happens to be imported first. RawJobPosting
    # is only ever used here as a type annotation (this module never
    # constructs or isinstance-checks it), and `from __future__ import
    # annotations` above means annotations are never evaluated at runtime,
    # so this is safe.
    from .connectors.base import RawJobPosting

# The example template's shipped placeholder (config.example.yaml). A
# config.yaml that still has exactly this means the user never edited it --
# treated the same as "blank"/no-signal by both resume-autopopulation
# (resume.py) and profile-based company filtering (company_categories.py),
# since a config that still says "Software Engineer" wasn't a deliberate
# choice and shouldn't be read as one.
EXAMPLE_TARGET_TITLES = ["Software Engineer", "Backend Engineer"]
from .llm.base import LLMClient, NullLLMClient

logger = logging.getLogger(__name__)


class LLMScoringFailedError(RuntimeError):
    """Raised by score_fit when a configured (not llm.provider = "none")
    LLM call fails. The rule-based keyword fallback is only correct for the
    deliberate "no LLM configured" case -- silently substituting it for a
    *configured* LLM that's unreachable (Ollama not running, a bad API key,
    etc.) produces much weaker scores with no indication anything went
    wrong, one job at a time. Callers (run_search_cycle) catch this to
    pause the search and prompt the user to fix the LLM instead."""

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
# Checked in this order (most specific/highest level first) so a title
# containing more than one signal resolves to the more senior one -- e.g.
# "Senior Vice President" matches "exec", not "senior"; "Senior Staff
# Engineer" matches "staff", not "senior".
SENIORITY_TITLE_PATTERNS = {
    "exec": r"\b(vp|vice president|chief|cto|ceo|coo|svp|evp|head of)\b",
    "principal": r"\bprincipal\b|\bdistinguished\b",
    "staff": r"\bstaff\b",
    # "senior"/"sr" were missing here entirely until this was added --
    # meaning the single most common level word in real job titles (and
    # the one candidates most often filter on) was never detected at all,
    # silently skipping the seniority check below for any title using it.
    "senior": r"\bsenior\b|\bsr\.?\b",
    "junior": r"\bjunior\b|\bjr\.?\b|\bentry[- ]level\b|\bassociate\b",
    "mid": r"\bmid[- ]level\b|\bmid[- ]tier\b",
    "intern": r"\bintern(ship)?\b",
}
# "Role II"/"Role III"-style numbered leveling (e.g. "Software Engineer
# II", "Data Analyst III") is common at large/enterprise employers in
# place of a level word, and was invisible to detection entirely before
# this -- mapped using the common I=junior/II=mid/III=senior/IV=staff/
# V=principal convention. Approximate (numbering schemes vary by company),
# but a reasonable-by-default guess beats detecting nothing for the many
# titles that use this convention instead of a level word.
_NUMERAL_LEVEL_TO_SENIORITY = {"i": "junior", "ii": "mid", "iii": "senior", "iv": "staff", "v": "principal"}
_NUMERAL_LEVEL_PATTERN = re.compile(
    r"\b(?:engineer|developer|analyst|scientist|designer|specialist|architect|manager)\s+(i|ii|iii|iv|v)\b"
)
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
    numeral_match = _NUMERAL_LEVEL_PATTERN.search(title_lower)
    if numeral_match:
        return _NUMERAL_LEVEL_TO_SENIORITY.get(numeral_match.group(1))
    return None


EMPLOYMENT_TYPES = ["full_time", "part_time", "contract", "internship"]

# Common dealbreakers offered as checkboxes in Settings, rather than making
# everyone type them out as free text -- (key, label) so the key is a stable
# form-field/storage suffix independent of the label's exact wording. The
# label itself is what's actually stored in Preferences.dealbreakers (same
# substring-match and LLM-prompt handling as any custom, freely-typed
# dealbreaker), so picking a different label here is a wording change, not a
# schema change. Chosen to apply broadly across job types, not just tech
# roles -- "requires relocation" is deliberately excluded since
# willing_to_relocate already covers that.
COMMON_DEALBREAKERS = [
    ("on_call", "Requires on-call rotation"),
    ("travel", "Requires travel"),
    ("no_salary_disclosed", "No salary or compensation range disclosed"),
    ("weekend_holiday", "Requires weekend or holiday shifts"),
    ("security_clearance", "Requires a security clearance"),
    ("unpaid_equity_only", "Unpaid or equity-only compensation"),
    ("overnight_shifts", "Requires overnight or graveyard shifts"),
    ("personal_vehicle", "Requires a personal vehicle"),
]


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

    if job.remote:
        if not prefs.remote_ok:
            return "Remote posting, but remote work isn't accepted in your preferences"
    else:
        if not prefs.onsite_ok and not prefs.willing_to_relocate:
            return "On-site posting, but you haven't opted into on-site roles"
        # willing_to_relocate is the ONLY thing that should bypass the
        # locations list -- onsite_ok alone means "I'll consider on-site
        # roles," not "I'll consider on-site roles anywhere in the world."
        # This used to be `not onsite_ok and not willing_to_relocate`, which
        # meant setting onsite_ok=True (the common case for anyone open to
        # in-person work) skipped location matching entirely -- an on-site
        # posting in any city, anywhere, passed the prefilter regardless of
        # prefs.locations, and reached the LLM/dashboard already outside
        # the user's actual location preference.
        if not prefs.willing_to_relocate:
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
) -> tuple[float, str, str]:
    """Returns (score 0-100, rationale, method) where method is "llm" or
    "rule_based". The LLM checks the posting's required qualifications
    against the candidate's actual resume text (not just the compressed
    skills/titles summary). Two things force the score to 0 outright rather
    than just lowering it: a hit on one of the candidate's stated
    dealbreakers, or failing minimum/required qualifications (missing
    multiple required items, or one major one) — see SYSTEM_PROMPT.

    Rule-based (keyword-overlap) scoring is used ONLY for the deliberate
    llm.provider = "none" configuration. A *configured* LLM (Ollama,
    Anthropic) that fails raises LLMScoringFailedError instead of silently
    substituting the much weaker heuristic -- "it fell back to word-match
    scoring" with no indication anything was wrong was a real user report.
    Callers are expected to pause and let the user fix the LLM rather than
    catch this and fall back themselves."""
    if isinstance(llm, NullLLMClient):
        score, rationale = _rule_based_score(job, resume_summary, prefs)
        return score, rationale, "rule_based"

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
            if not _dealbreaker_plausible(job, prefs.dealbreakers):
                # A real incident: a model hallucinated "requires a security
                # clearance" as the dealbreaker hit on 50+ completely
                # unrelated software engineering postings -- a plausible-
                # sounding rationale every time, but the word "clearance"
                # didn't appear in a single one of those job descriptions.
                # dealbreaker_hit is a hard, score-zeroing disqualifier, so
                # trusting an ungrounded one is exactly as costly as
                # trusting a blank rationale. This is deliberately a loose
                # keyword-overlap check, not a requirement that the posting
                # use the dealbreaker's exact wording -- the LLM is still
                # trusted to recognize a dealbreaker phrased differently,
                # just not one with literally zero textual basis.
                raise ValueError(
                    f"LLM claimed a dealbreaker hit for {job.title!r} at {job.company!r} but none "
                    "of the candidate's stated dealbreakers have any keyword overlap with the "
                    "posting text -- treating as a hallucinated disqualification."
                )
            return 0.0, rationale or "Disqualified: posting appears to violate a stated dealbreaker.", "llm"

        if data.get("fails_minimum_requirements"):
            fallback = "Disqualified: missing minimum/required qualifications."
            if unmet:
                fallback = "Disqualified: missing required qualification(s): " + "; ".join(unmet)
            return 0.0, rationale or fallback, "llm"

        score = float(data.get("score", 0))
        if unmet and not rationale:
            rationale = "Unmet requirement(s): " + "; ".join(unmet)
        if not rationale:
            # SYSTEM_PROMPT requires a 1-3 sentence rationale on every
            # response; a valid-JSON reply with none (seen in practice as
            # score 0 with "rationale": "") is the model giving up under
            # Ollama's forced JSON-mode grammar, not a genuine "this is a
            # 0" judgment -- a real incident wiped ~95% of a job list's
            # scores to 0 with blank rationale this way, overwriting
            # previously-good scores with no indication anything was
            # wrong. Treated as a failed call (raises below) so the
            # caller's pause-and-retry gets a real answer instead of
            # silently trusting a degenerate one.
            raise ValueError(f"LLM returned no rationale alongside a score of {score}")
        return max(0.0, min(100.0, score)), rationale, "llm"
    except Exception as exc:  # noqa: BLE001 - normalized into one error type for callers to pause on
        raise LLMScoringFailedError(
            f"LLM fit scoring failed for {job.title!r} at {job.company!r}: {str(exc)[:300]}"
        ) from exc


_DEALBREAKER_STOPWORDS = {
    "a", "an", "the", "of", "to", "in", "on", "for", "with", "is", "are", "or", "and", "not",
    "that", "this", "any", "all", "has", "have", "will", "must", "require", "requires", "required",
}


def _dealbreaker_plausible(job: RawJobPosting, dealbreakers: list[str]) -> bool:
    """Loose keyword-overlap sanity check on an LLM's dealbreaker_hit claim
    -- not a requirement that the posting use the dealbreaker's exact
    wording (the LLM is still trusted to recognize one phrased
    differently), just that at least one substantive word from some
    stated dealbreaker appears anywhere in the posting's own text. Catches
    a hallucinated dealbreaker with zero textual basis without rejecting a
    real one worded differently than the user's own phrasing."""
    text = f"{job.title} {job.description}".lower()
    for dealbreaker in dealbreakers:
        words = [w for w in re.findall(r"[a-z]+", dealbreaker.lower()) if len(w) > 3 and w not in _DEALBREAKER_STOPWORDS]
        if any(w in text for w in words):
            return True
    return False


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
        "Rule-based score (no LLM configured): "
        f"{skill_hits} skill keyword(s) and {boost_hits} boost keyword(s) matched."
    )
