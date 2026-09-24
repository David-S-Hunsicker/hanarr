"""Loads a resume file and extracts a structured profile from it via the
configured LLM. If no LLM is configured (llm.provider = 'none'), the raw
text is still stored and used for keyword matching — just without the
structured skills/titles/years extraction.
"""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from .config import Preferences, Settings
from .db import get_or_create_profile
from .llm.base import LLMClient
from .models import Profile

ALLOWED_RESUME_EXTENSIONS = {".pdf", ".txt", ".md"}

# The example template's shipped values (config.example.yaml). A config.yaml
# that still has exactly these means the user never edited them — treated
# the same as "blank" for auto-populating from the resume, since a config
# that still says "Software Engineer" / "python" wasn't a deliberate choice.
_EXAMPLE_TARGET_TITLES = ["Software Engineer", "Backend Engineer"]
_EXAMPLE_KEYWORDS_BOOST = ["python", "distributed systems"]

MAX_AUTO_BOOST_KEYWORDS = 10
MAX_RESUME_BYTES = 10 * 1024 * 1024


def autopopulate_preferences_from_resume(prefs: Preferences, resume_summary: dict) -> bool:
    """If target_titles/keywords_boost are blank or still the example
    template's default values, fill them in from the resume's extracted
    titles/skills. Mutates `prefs` in place; returns True if it changed
    anything (so the caller knows whether to persist config.yaml)."""
    changed = False

    if not prefs.target_titles or prefs.target_titles == _EXAMPLE_TARGET_TITLES:
        titles = resume_summary.get("titles") or []
        if titles:
            prefs.target_titles = list(titles)
            changed = True

    if not prefs.keywords_boost or prefs.keywords_boost == _EXAMPLE_KEYWORDS_BOOST:
        skills = resume_summary.get("skills") or []
        if skills:
            prefs.keywords_boost = list(skills[:MAX_AUTO_BOOST_KEYWORDS])
            changed = True

    return changed

SYSTEM_PROMPT = """You are extracting a structured profile from a resume for a job-matching \
tool. Read the resume text and return ONLY a JSON object with these fields:

{
  "titles": ["job titles this person has held or is qualified for"],
  "years_experience": <integer, best estimate total professional experience>,
  "skills": ["key skills, tools, and technologies, most relevant first"],
  "industries": ["industries this person has worked in"],
  "seniority": "one of: intern, junior, mid, senior, staff, principal, exec",
  "summary": "2-3 sentence plain-language summary of this candidate"
}

Return valid JSON only, no other text."""


def load_resume_text(resume_path: Path | str) -> str:
    path = Path(resume_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Resume not found at {path}. Update profile.resume_path in "
            f"config.yaml, or run `jobcopilot init` to set it up."
        )
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    return path.read_text(encoding="utf-8", errors="ignore")


def extract_profile_summary(resume_text: str, llm: LLMClient) -> dict:
    """Returns the structured dict described in SYSTEM_PROMPT. On any LLM
    or parse failure, returns a minimal fallback so the rest of the
    pipeline can keep running on raw-text keyword matching alone."""
    try:
        raw = llm.complete_json(SYSTEM_PROMPT, resume_text[:12000])
        data = json.loads(raw)
        data.setdefault("titles", [])
        data.setdefault("years_experience", None)
        data.setdefault("skills", [])
        data.setdefault("industries", [])
        data.setdefault("seniority", "mid")
        data.setdefault("summary", "")
        return data
    except Exception as e:  # noqa: BLE001 - deliberately broad, this is a best-effort fallback
        return {
            "titles": [],
            "years_experience": None,
            "skills": [],
            "industries": [],
            "seniority": "mid",
            "summary": "",
            "_extraction_error": str(e),
        }


BOOST_KEYWORDS_SYSTEM_PROMPT = """You are helping a job-search tool build a "boost keywords" \
list — words that, when found in a job posting, indicate a strong fit and should raise that \
posting's score before anything is sent to a more expensive LLM-based reviewer. Given the \
candidate's resume text and the job titles they're targeting, return ONLY a JSON object:

{
  "keywords": ["10-15 specific skills, tools, technologies, or methodologies from the resume \
that are most distinctive of this candidate and most likely to appear in postings for their \
target roles"]
}

Prefer specific, searchable terms (e.g. "Kubernetes", "distributed systems", "PyTorch") over \
vague ones (e.g. "hard worker", "team player", "communication"). Prefer terms that actually \
appear in or are clearly implied by the resume — don't invent skills the candidate doesn't \
have. Order from most to least distinctive/important."""


def suggest_boost_keywords(resume_text: str, target_titles: list[str], llm: LLMClient) -> list[str]:
    """Asks the LLM to propose a boost-keywords list for the prefilter,
    tailored to the candidate's resume and the roles they're targeting —
    distinct from extract_profile_summary's generic skills list, since this
    is scoped to what's actually useful for filtering incoming postings.
    Raises on LLM/parse failure; the caller decides how to surface that."""
    user_prompt = json.dumps(
        {
            "resume_text": resume_text[:12000],
            "target_titles": target_titles,
        }
    )
    raw = llm.complete_json(BOOST_KEYWORDS_SYSTEM_PROMPT, user_prompt)
    data = json.loads(raw)
    keywords = data.get("keywords") or []
    if not isinstance(keywords, list):
        raise ValueError(f"Expected a list of keywords, got: {keywords!r}")
    return [str(k).strip() for k in keywords if str(k).strip()]


def parse_and_store_resume(
    session: Session, settings: Settings, llm: LLMClient
) -> tuple[Profile, dict, bool]:
    """Reads settings.profile.resume_path, extracts a structured summary via
    the LLM, and stores both the raw text and summary on the profile. Shared
    by `jobcopilot init` and the dashboard's upload/re-parse flow so the two
    surfaces can't drift. Also auto-populates target_titles/keywords_boost
    on settings.preferences (mutated in place) if they're still blank or the
    example template's defaults — the third return value says whether that
    happened, so the caller knows whether to persist config.yaml. Raises
    FileNotFoundError if the resume is missing; LLM extraction failures are
    caught and recorded in the returned summary's _extraction_error, not
    raised."""
    resume_path = Path(settings.profile.resume_path)
    resume_text = load_resume_text(resume_path)
    summary = extract_profile_summary(resume_text, llm)

    profile = get_or_create_profile(session, settings)
    profile.resume_text = resume_text
    profile.resume_summary_json = json.dumps(summary)
    session.commit()

    prefs_changed = False
    if not summary.get("_extraction_error"):
        prefs_changed = autopopulate_preferences_from_resume(settings.preferences, summary)

    return profile, summary, prefs_changed
