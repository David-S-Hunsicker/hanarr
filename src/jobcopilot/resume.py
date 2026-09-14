"""Loads a resume file and extracts a structured profile from it via the
configured LLM. If no LLM is configured (llm.provider = 'none'), the raw
text is still stored and used for keyword matching — just without the
structured skills/titles/years extraction.
"""
from __future__ import annotations

import json
from pathlib import Path

from .llm.base import LLMClient

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
