"""Best-effort skill evidence from a GitHub profile URL found in a resume.

Niche case (mainly software engineers): if the resume text references a
github.com/<username> profile, that user's public, non-fork repositories'
primary languages become additional skill evidence (source="github") --
without ever overriding evidence the resume or the user already provided.
This only calls GitHub's public REST API over HTTPS, the same kind of
read-only access the job connectors and coaching-submission review already
use elsewhere in this app.
"""
from __future__ import annotations

import re

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Profile, ProfileSkill, Skill

GITHUB_API_BASE = "https://api.github.com"
GITHUB_FETCH_TIMEOUT = 15.0
MAX_REPOS_CONSIDERED = 100
MAX_LANGUAGES_SYNCED = 8

_USERNAME_PATTERN = re.compile(
    r"github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?)", re.IGNORECASE
)

# GitHub path segments that show up in resumes as generic links (e.g.
# "see github.com/features/actions") and are never a person's username.
_RESERVED_PATH_SEGMENTS = {
    "features", "marketplace", "sponsors", "orgs", "apps", "topics",
    "settings", "notifications", "issues", "pulls", "search", "explore",
    "about", "pricing", "login", "join", "contact", "security", "enterprise",
    "collections", "trending", "watching", "stars", "site",
}


def detect_github_username(resume_text: str) -> str | None:
    """Returns the first plausible GitHub username referenced as a
    github.com profile/repo URL in the resume text, or None."""
    for match in _USERNAME_PATTERN.finditer(resume_text or ""):
        username = match.group(1)
        if username.lower() not in _RESERVED_PATH_SEGMENTS:
            return username
    return None


def fetch_public_repo_languages(
    username: str, client: httpx.Client | None = None
) -> dict[str, int]:
    """Counts the primary language of each of a GitHub user's public,
    non-fork repositories. Raises ValueError on any failure the caller
    should treat as "could not fetch" -- never returns partial data."""
    http_client = client or httpx.Client(
        timeout=GITHUB_FETCH_TIMEOUT,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "hanarr-resume-scan"},
    )
    try:
        response = http_client.get(
            f"{GITHUB_API_BASE}/users/{username}/repos"
            f"?per_page={MAX_REPOS_CONSIDERED}&type=owner&sort=updated"
        )
        if response.status_code == 404:
            raise ValueError(f"GitHub user '{username}' was not found.")
        if response.status_code == 403:
            raise ValueError("GitHub API request was rate-limited or forbidden; try again later.")
        response.raise_for_status()
        repos = response.json()
        if not isinstance(repos, list):
            raise ValueError("GitHub API returned an unexpected response.")
    finally:
        if client is None:
            http_client.close()

    counts: dict[str, int] = {}
    for repo in repos:
        if not isinstance(repo, dict) or repo.get("fork"):
            continue
        language = repo.get("language")
        if language:
            counts[language] = counts.get(language, 0) + 1
    return counts


def _skill(session: Session, name: str) -> Skill:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    skill = session.execute(select(Skill).where(Skill.slug == slug)).scalar_one_or_none()
    if skill is None:
        skill = Skill(name=name.strip(), slug=slug)
        session.add(skill)
        session.flush()
    return skill


def sync_github_skills(
    session: Session, profile: Profile, language_counts: dict[str, int], username: str,
) -> list[Skill]:
    """Adds ProfileSkill rows (source="github") for the most-used languages
    across a user's public repos -- only for skills with no existing
    evidence yet, so it never overrides a resume-stated or manually-set
    skill. Confidence scales with repo count, capped at 0.6 since it's
    inferred from code, not asserted."""
    ranked = sorted(language_counts.items(), key=lambda kv: kv[1], reverse=True)
    result = []
    for name, count in ranked[:MAX_LANGUAGES_SYNCED]:
        skill = _skill(session, name)
        result.append(skill)
        existing = session.execute(
            select(ProfileSkill).where(
                ProfileSkill.profile_id == profile.id, ProfileSkill.skill_id == skill.id
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(ProfileSkill(
                profile_id=profile.id,
                skill_id=skill.id,
                source="github",
                confidence=min(0.6, 0.3 + 0.1 * count),
                evidence=(
                    f"Primary language in {count} public "
                    f"repositor{'y' if count == 1 else 'ies'} on GitHub ({username})"
                ),
            ))
    session.flush()
    return result
