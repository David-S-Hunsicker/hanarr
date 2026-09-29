import json

import httpx
import pytest
from sqlalchemy import select

from hanarr.config import Settings
from hanarr.db import get_or_create_profile, make_session_factory
from hanarr.github_skills import (
    detect_github_username,
    fetch_public_repo_languages,
    sync_github_skills,
)
from hanarr.models import ProfileSkill, Skill


class FakeGitHubClient:
    def __init__(self, response: httpx.Response):
        self.response = response
        self.requested: list[str] = []
        self.closed = False

    def get(self, url: str) -> httpx.Response:
        self.requested.append(url)
        self.response.request = httpx.Request("GET", url)
        return self.response

    def close(self) -> None:
        self.closed = True


def _settings(tmp_path):
    return Settings(data_dir=tmp_path / "data")


def test_detect_github_username_finds_a_profile_url_in_resume_text():
    text = "Portfolio: https://github.com/octocat and LinkedIn: ..."
    assert detect_github_username(text) == "octocat"


def test_detect_github_username_ignores_reserved_github_path_segments():
    text = "See github.com/features/actions for CI docs."
    assert detect_github_username(text) is None


def test_detect_github_username_returns_none_when_absent():
    assert detect_github_username("A resume with no GitHub link at all.") is None


def test_fetch_public_repo_languages_counts_primary_language_excluding_forks():
    repos = [
        {"language": "Python", "fork": False},
        {"language": "Python", "fork": False},
        {"language": "TypeScript", "fork": False},
        {"language": "Java", "fork": True},
        {"language": None, "fork": False},
    ]
    client = FakeGitHubClient(httpx.Response(200, json=repos))
    counts = fetch_public_repo_languages("octocat", client=client)
    assert counts == {"Python": 2, "TypeScript": 1}
    assert client.requested[0].endswith("/users/octocat/repos?per_page=100&type=owner&sort=updated")


def test_fetch_public_repo_languages_raises_on_missing_user():
    client = FakeGitHubClient(httpx.Response(404, json={}))
    with pytest.raises(ValueError, match="not found"):
        fetch_public_repo_languages("nobody", client=client)


def test_fetch_public_repo_languages_raises_on_rate_limit():
    client = FakeGitHubClient(httpx.Response(403, json={}))
    with pytest.raises(ValueError, match="rate-limited"):
        fetch_public_repo_languages("octocat", client=client)


def test_sync_github_skills_creates_profile_skills_ranked_by_frequency(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        sync_github_skills(session, profile, {"Python": 5, "TypeScript": 1}, "octocat")
        session.commit()

        rows = session.execute(select(ProfileSkill)).scalars().all()
        by_skill_name = {
            session.get(Skill, row.skill_id).name: row for row in rows
        }
        assert set(by_skill_name) == {"Python", "TypeScript"}
        assert by_skill_name["Python"].source == "github"
        assert "5 public repositories on GitHub (octocat)" in by_skill_name["Python"].evidence
        assert by_skill_name["Python"].confidence > by_skill_name["TypeScript"].confidence


def test_sync_github_skills_never_overrides_an_existing_profile_skill(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        skill = Skill(name="Python", slug="python")
        session.add(skill)
        session.flush()
        session.add(ProfileSkill(
            profile_id=profile.id, skill_id=skill.id, source="resume",
            confidence=0.9, evidence="Explicitly listed on the resume",
        ))
        session.commit()

        sync_github_skills(session, profile, {"Python": 10}, "octocat")
        session.commit()

        rows = session.execute(
            select(ProfileSkill).where(ProfileSkill.profile_id == profile.id)
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].source == "resume"
        assert rows[0].evidence == "Explicitly listed on the resume"


def test_sync_github_skills_caps_the_number_of_languages_synced(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        counts = {f"Lang{i}": i for i in range(1, 12)}
        result = sync_github_skills(session, profile, counts, "octocat")
        session.commit()
        assert len(result) == 8
