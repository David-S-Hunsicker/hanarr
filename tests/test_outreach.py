import json

from hanarr.llm.base import LLMClient
from hanarr.models import JobPosting, Profile
from hanarr.outreach import draft_outreach_email, generate_and_store_outreach_email


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


def test_draft_outreach_email_uses_the_llm_response_when_available():
    profile = Profile(name="Test", resume_text="Built distributed systems in Python.")
    job = _job()
    llm = _FakeLLM({"email": "Hi Jane, I'd love to chat about the role."})

    content, source = draft_outreach_email(profile, job, "Jane Doe", llm)

    assert source == "llm"
    assert content == "Hi Jane, I'd love to chat about the role."


def test_draft_outreach_email_falls_back_to_deterministic_template_on_llm_failure():
    profile = Profile(name="Test", resume_summary_json=json.dumps({"skills": ["Python", "Kubernetes"]}))
    job = _job(company="Acme", title="Backend Engineer")

    content, source = draft_outreach_email(profile, job, "jane@example.com", _RaisingLLM())

    assert source == "deterministic"
    assert "Acme" in content
    assert "Backend Engineer" in content
    assert "Python" in content


def test_draft_outreach_email_falls_back_on_malformed_llm_response():
    profile = Profile(name="Test")
    job = _job()
    llm = _FakeLLM({"email": "   "})  # blank after strip

    content, source = draft_outreach_email(profile, job, "Jane Doe", llm)

    assert source == "deterministic"
    assert content  # never empty


def test_deterministic_fallback_never_fabricates_skills_when_none_are_recorded():
    profile = Profile(name="Test")  # no resume_summary_json at all
    job = _job()

    content, source = draft_outreach_email(profile, job, "Jane Doe", _RaisingLLM())

    assert source == "deterministic"
    assert "my background" in content


def test_deterministic_fallback_derives_a_greeting_name_from_an_email_or_linkedin_url():
    profile = Profile(name="Test")
    job = _job()

    by_email, _ = draft_outreach_email(profile, job, "jane.doe@example.com", _RaisingLLM())
    assert "Hi jane.doe," in by_email

    by_linkedin, _ = draft_outreach_email(profile, job, "https://linkedin.com/in/janedoe", _RaisingLLM())
    assert "Hi janedoe," in by_linkedin


def test_generate_and_store_outreach_email_persists_contact_content_source_and_timestamp():
    profile = Profile(name="Test", resume_text="Python engineer.")
    job = _job()
    llm = _FakeLLM({"email": "Hi Jane, ..."})

    generate_and_store_outreach_email(profile, job, "Jane Doe", llm)

    assert job.outreach_contact == "Jane Doe"
    assert job.outreach_email == "Hi Jane, ..."
    assert job.outreach_email_source == "llm"
    assert job.outreach_email_generated_at is not None


def test_generate_and_store_outreach_email_overwrites_a_previous_draft():
    profile = Profile(name="Test")
    job = _job(outreach_contact="Old Contact", outreach_email="old draft", outreach_email_source="llm")

    generate_and_store_outreach_email(profile, job, "New Contact", _RaisingLLM())

    assert job.outreach_contact == "New Contact"
    assert job.outreach_email != "old draft"
    assert job.outreach_email_source == "deterministic"


def test_draft_outreach_email_never_invents_facts_about_the_contact():
    """The system prompt explicitly forbids this -- a regression test can't
    verify prompt-following against a real LLM, but it pins the prompt
    text itself so a future edit can't silently drop the instruction."""
    from hanarr.outreach import SYSTEM_PROMPT
    assert "never invent anything about the contact" in SYSTEM_PROMPT
