import json

from hanarr.cover_letter import draft_cover_letter, generate_and_store_cover_letter
from hanarr.llm.base import LLMClient
from hanarr.models import JobPosting, Profile


class _FakeLLM(LLMClient):
    def __init__(self, response):
        self.response = response

    def complete_json(self, system: str, user: str) -> str:
        if isinstance(self.response, Exception):
            raise self.response
        return json.dumps(self.response)


class _RaisingLLM(LLMClient):
    def complete_json(self, system: str, user: str) -> str:
        raise RuntimeError("model unreachable")


def _job(**overrides) -> JobPosting:
    defaults = dict(
        profile_id=1, source="test", external_id="1", company="Acme",
        title="Backend Engineer", url="https://example.test/1", description="Python required.",
    )
    defaults.update(overrides)
    return JobPosting(**defaults)


def test_draft_cover_letter_uses_the_llm_response_when_available():
    profile = Profile(name="Test", resume_text="Built distributed systems in Python.")
    job = _job()
    llm = _FakeLLM({"cover_letter": "Dear Acme, I'd love to join your team."})

    content, source = draft_cover_letter(profile, job, llm)

    assert source == "llm"
    assert content == "Dear Acme, I'd love to join your team."


def test_draft_cover_letter_falls_back_to_deterministic_template_on_llm_failure():
    profile = Profile(name="Test", resume_summary_json=json.dumps({"skills": ["Python", "Kubernetes"]}))
    job = _job(company="Acme", title="Backend Engineer")

    content, source = draft_cover_letter(profile, job, _RaisingLLM())

    assert source == "deterministic"
    assert "Acme" in content
    assert "Backend Engineer" in content
    assert "Python" in content


def test_draft_cover_letter_falls_back_on_malformed_llm_response():
    profile = Profile(name="Test")
    job = _job()
    llm = _FakeLLM({"cover_letter": "   "})  # blank after strip

    content, source = draft_cover_letter(profile, job, llm)

    assert source == "deterministic"
    assert content  # never empty


def test_deterministic_fallback_never_fabricates_skills_when_none_are_recorded():
    profile = Profile(name="Test")  # no resume_summary_json at all
    job = _job()

    content, source = draft_cover_letter(profile, job, _RaisingLLM())

    assert source == "deterministic"
    assert "My resume outlines the experience" in content


def test_generate_and_store_cover_letter_persists_content_source_and_timestamp():
    profile = Profile(name="Test", resume_text="Python engineer.")
    job = _job()
    llm = _FakeLLM({"cover_letter": "Dear Acme team, ..."})

    generate_and_store_cover_letter(profile, job, llm)

    assert job.cover_letter == "Dear Acme team, ..."
    assert job.cover_letter_source == "llm"
    assert job.cover_letter_generated_at is not None


def test_generate_and_store_cover_letter_overwrites_a_previous_draft():
    profile = Profile(name="Test")
    job = _job(cover_letter="old draft", cover_letter_source="llm")

    generate_and_store_cover_letter(profile, job, _RaisingLLM())

    assert job.cover_letter != "old draft"
    assert job.cover_letter_source == "deterministic"
