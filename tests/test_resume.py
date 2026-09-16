import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from jobcopilot.config import Preferences, Settings
from jobcopilot.llm.base import LLMClient
from jobcopilot.models import Base
from jobcopilot.resume import (
    autopopulate_preferences_from_resume,
    parse_and_store_resume,
    suggest_boost_keywords,
)


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
