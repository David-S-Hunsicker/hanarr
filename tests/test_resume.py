import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from hanarr.config import Preferences, Settings
from hanarr.llm.base import LLMClient
from hanarr.models import Base, ResumeVersion
from hanarr.resume import (
    autopopulate_preferences_from_resume,
    parse_and_store_resume,
    suggest_boost_keywords,
)
from hanarr.resume_loop import resume_status


def test_autopopulate_fills_blank_preferences():
    prefs = Preferences(target_titles=[], keywords_boost=[])
    summary = {"titles": ["AI Engineer", "ML Engineer"], "skills": ["python", "pytorch"]}

    changed = autopopulate_preferences_from_resume(prefs, summary)

    assert changed is True
    assert prefs.target_titles == ["AI Engineer", "ML Engineer"]
    assert prefs.keywords_boost == ["python", "pytorch"]


def test_autopopulate_overwrites_example_template_defaults():
    prefs = Preferences(
        target_titles=["Software Engineer", "Backend Engineer"],
        keywords_boost=["python", "distributed systems"],
    )
    summary = {"titles": ["AI Engineer"], "skills": ["pytorch"]}

    changed = autopopulate_preferences_from_resume(prefs, summary)

    assert changed is True
    assert prefs.target_titles == ["AI Engineer"]
    assert prefs.keywords_boost == ["pytorch"]


def test_autopopulate_leaves_customized_preferences_untouched():
    prefs = Preferences(target_titles=["Staff Engineer"], keywords_boost=["rust", "kubernetes"])
    summary = {"titles": ["AI Engineer"], "skills": ["pytorch"]}

    changed = autopopulate_preferences_from_resume(prefs, summary)

    assert changed is False
    assert prefs.target_titles == ["Staff Engineer"]
    assert prefs.keywords_boost == ["rust", "kubernetes"]


def test_autopopulate_caps_boost_keywords_at_ten():
    prefs = Preferences(target_titles=[], keywords_boost=[])
    summary = {"skills": [f"skill{i}" for i in range(25)]}

    autopopulate_preferences_from_resume(prefs, summary)

    assert len(prefs.keywords_boost) == 10
    assert prefs.keywords_boost == [f"skill{i}" for i in range(10)]


def test_autopopulate_no_op_when_resume_summary_empty():
    prefs = Preferences(target_titles=[], keywords_boost=[])

    changed = autopopulate_preferences_from_resume(prefs, {})

    assert changed is False
    assert prefs.target_titles == []
    assert prefs.keywords_boost == []


class _FakeLLM(LLMClient):
    def __init__(self, response):
        self.response = response
        self.last_user_prompt = None

    def complete_json(self, system: str, user: str) -> str:
        self.last_user_prompt = user
        if isinstance(self.response, Exception):
            raise self.response
        return json.dumps(self.response)


def test_suggest_boost_keywords_returns_llm_list():
    llm = _FakeLLM({"keywords": ["pytorch", "kubernetes", "llm agents"]})

    keywords = suggest_boost_keywords("Resume mentioning PyTorch and Kubernetes.", ["AI Engineer"], llm)

    assert keywords == ["pytorch", "kubernetes", "llm agents"]


def test_suggest_boost_keywords_sends_resume_and_titles_to_llm():
    llm = _FakeLLM({"keywords": ["x"]})

    suggest_boost_keywords("10 years of distributed systems experience", ["Backend Engineer"], llm)

    payload = json.loads(llm.last_user_prompt)
    assert "10 years of distributed systems" in payload["resume_text"]
    assert payload["target_titles"] == ["Backend Engineer"]


def test_suggest_boost_keywords_strips_blank_and_non_string_entries():
    llm = _FakeLLM({"keywords": ["python", "  ", "", "kubernetes"]})

    keywords = suggest_boost_keywords("resume text", [], llm)

    assert keywords == ["python", "kubernetes"]


def test_suggest_boost_keywords_raises_on_malformed_response():
    llm = _FakeLLM({"keywords": "not a list"})

    with pytest.raises(ValueError):
        suggest_boost_keywords("resume text", [], llm)


def test_suggest_boost_keywords_propagates_llm_errors():
    llm = _FakeLLM(RuntimeError("model unreachable"))

    with pytest.raises(RuntimeError):
        suggest_boost_keywords("resume text", [], llm)


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_parse_and_store_resume_saves_text_and_summary_and_updates_preferences(tmp_path):
    resume_file = tmp_path / "resume.txt"
    resume_file.write_text("Jane Doe. AI Engineer with PyTorch and Kubernetes experience.")

    settings = Settings()
    settings.profile.resume_path = str(resume_file)
    llm = _FakeLLM({"titles": ["AI Engineer"], "skills": ["pytorch", "kubernetes"]})
    session = _make_session()

    profile, summary, prefs_changed = parse_and_store_resume(session, settings, llm)

    assert "PyTorch" in profile.resume_text
    assert json.loads(profile.resume_summary_json)["titles"] == ["AI Engineer"]
    assert summary["titles"] == ["AI Engineer"]
    assert prefs_changed is True
    assert settings.preferences.target_titles == ["AI Engineer"]
    assert settings.preferences.keywords_boost == ["pytorch", "kubernetes"]


def test_parse_and_store_resume_records_original_filename_and_parsed_time(tmp_path):
    """Regression test: the file is always stored under a fixed internal
    name (resumes/resume.pdf), so settings.profile.resume_path alone can't
    tell a user what they actually uploaded -- the "Current resume" display
    showed that internal path and nothing else, which told the user
    nothing. original_filename lets the true browser-supplied name survive
    the rename."""
    import datetime as dt

    resume_file = tmp_path / "resume.txt"
    resume_file.write_text("Jane Doe. AI Engineer.")
    settings = Settings()
    settings.profile.resume_path = str(resume_file)
    session = _make_session()
    before = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)

    profile, _, _ = parse_and_store_resume(
        session, settings, _FakeLLM({"titles": [], "skills": []}), original_filename="David_Resume_2026.pdf"
    )

    assert profile.resume_original_filename == "David_Resume_2026.pdf"
    assert profile.resume_parsed_at is not None
    assert profile.resume_parsed_at >= before


def test_parse_and_store_resume_falls_back_to_path_name_without_explicit_filename(tmp_path):
    """`hanarr init` never has a separate "original" name -- the configured
    resume_path is the only name there ever was, so it's used directly."""
    resume_file = tmp_path / "my-resume.md"
    resume_file.write_text("Jane Doe.")
    settings = Settings()
    settings.profile.resume_path = str(resume_file)
    session = _make_session()

    profile, _, _ = parse_and_store_resume(session, settings, _FakeLLM({"titles": [], "skills": []}))

    assert profile.resume_original_filename == "my-resume.md"


def test_parse_and_store_resume_does_not_touch_preferences_on_extraction_failure(tmp_path):
    resume_file = tmp_path / "resume.txt"
    resume_file.write_text("Jane Doe.")

    settings = Settings()
    settings.profile.resume_path = str(resume_file)
    llm = _FakeLLM(RuntimeError("model unreachable"))
    session = _make_session()

    profile, summary, prefs_changed = parse_and_store_resume(session, settings, llm)

    assert summary.get("_extraction_error")
    assert prefs_changed is False
    assert profile.resume_text == "Jane Doe."


def test_parse_and_store_resume_raises_on_missing_file():
    settings = Settings()
    settings.profile.resume_path = "resumes/does_not_exist.txt"
    session = _make_session()

    with pytest.raises(FileNotFoundError):
        parse_and_store_resume(session, settings, _FakeLLM({}))


def test_parse_and_store_resume_creates_a_resume_version_the_resume_page_can_see(tmp_path):
    """Regression test: the Resume page and the project-completion proposal
    loop are both driven entirely by ResumeVersion rows, not
    profile.resume_text directly. Before this fix, uploading a resume (or
    running `hanarr init`) only ever wrote profile.resume_text/summary_json
    and never created a ResumeVersion, so the Resume page permanently showed
    "not ready" no matter how many times a resume was (re-)uploaded."""
    resume_file = tmp_path / "resume.txt"
    resume_file.write_text("Jane Doe. AI Engineer with PyTorch experience.")

    settings = Settings()
    settings.profile.resume_path = str(resume_file)
    llm = _FakeLLM({"titles": ["AI Engineer"], "skills": ["pytorch"]})
    session = _make_session()

    profile, _, _ = parse_and_store_resume(session, settings, llm)
    session.commit()

    versions = session.query(ResumeVersion).filter_by(profile_id=profile.id).all()
    assert len(versions) == 1
    assert versions[0].is_active is True
    assert "PyTorch" in versions[0].content

    status = resume_status(profile, session)
    assert status["matcher"]["status"] == "ready"
    assert status["matcher"]["version_id"] == versions[0].id
    assert "PyTorch" in status["active"]["content"]
    assert len(status["versions"]) == 1


def test_reparsing_an_unchanged_resume_does_not_pile_up_duplicate_versions(tmp_path):
    resume_file = tmp_path / "resume.txt"
    resume_file.write_text("Jane Doe. AI Engineer.")

    settings = Settings()
    settings.profile.resume_path = str(resume_file)
    llm = _FakeLLM({"titles": ["AI Engineer"], "skills": []})
    session = _make_session()

    profile, _, _ = parse_and_store_resume(session, settings, llm)
    parse_and_store_resume(session, settings, llm)  # same file content again
    session.commit()

    versions = session.query(ResumeVersion).filter_by(profile_id=profile.id).all()
    assert len(versions) == 1

    resume_file.write_text("Jane Doe. AI Engineer. Now with Kubernetes.")
    parse_and_store_resume(session, settings, llm)  # genuinely new content
    session.commit()

    versions = session.query(ResumeVersion).filter_by(profile_id=profile.id).all()
    assert len(versions) == 2
    active = [v for v in versions if v.is_active]
    assert len(active) == 1
    assert "Kubernetes" in active[0].content
