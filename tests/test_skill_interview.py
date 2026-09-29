import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from hanarr.config import Settings
from hanarr.dashboard.app import create_app
import hanarr.dashboard.app as app_module
from hanarr.db import get_or_create_profile, make_session_factory
from hanarr.models import ProfileSkill, Skill, SkillInterview
from hanarr.skill_interview import start_interview, submit_interview


class FakeLLM:
    def __init__(self, response):
        self.response = response

    def complete_json(self, system, user):
        return self.response


def _settings(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    settings.llm.provider = "none"
    return settings


def _skill(session, name="Kubernetes"):
    skill = Skill(name=name, slug=name.lower())
    session.add(skill)
    session.commit()
    return skill


def test_start_interview_uses_llm_questions_when_valid(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        skill = _skill(session)
        llm = FakeLLM(json.dumps({"questions": ["Describe a real outage you debugged.", "What's a common pitfall?"]}))
        interview = start_interview(session, profile.id, skill.id, llm)
        session.commit()
        assert interview.evaluator == "llm"
        assert json.loads(interview.questions_json) == [
            "Describe a real outage you debugged.", "What's a common pitfall?",
        ]
        assert interview.verdict is None


def test_start_interview_falls_back_to_deterministic_questions_on_bad_llm_output(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        skill = _skill(session)
        interview = start_interview(session, profile.id, skill.id, FakeLLM("not json"))
        session.commit()
        assert interview.evaluator == "deterministic"
        questions = json.loads(interview.questions_json)
        assert 2 <= len(questions) <= 4
        assert "Kubernetes" in questions[0]


def test_submit_interview_solid_verdict_creates_interview_sourced_profile_skill(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        skill = _skill(session)
        interview = start_interview(session, profile.id, skill.id, FakeLLM("not json"))
        session.commit()
        interview_id, skill_id = interview.id, skill.id

        answers = ["Used it to debug a rollout failure.", "Forgetting resource limits."]
        eval_llm = FakeLLM(json.dumps({
            "verdict": "solid", "feedback": "Answers show real hands-on depth.", "resources": [],
        }))
        result = submit_interview(session, profile.id, interview_id, answers, eval_llm)
        session.commit()
        assert result.verdict == "solid"
        assert result.evaluator == "llm"

        profile_skill = session.execute(
            select(ProfileSkill).where(ProfileSkill.skill_id == skill_id)
        ).scalar_one()
        assert profile_skill.source == "interview"
        assert profile_skill.confidence == 0.75


def test_submit_interview_never_lowers_a_higher_trust_confidence(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        skill = _skill(session)
        session.add(ProfileSkill(
            profile_id=profile.id, skill_id=skill.id, source="resume",
            confidence=0.9, evidence="Extracted from the stored resume profile summary",
        ))
        session.commit()
        interview = start_interview(session, profile.id, skill.id, FakeLLM("not json"))
        session.commit()

        eval_llm = FakeLLM(json.dumps({"verdict": "solid", "feedback": "Solid.", "resources": []}))
        submit_interview(session, profile.id, interview.id, ["a", "b"], eval_llm)
        session.commit()

        profile_skill = session.execute(
            select(ProfileSkill).where(ProfileSkill.skill_id == skill.id)
        ).scalar_one()
        assert profile_skill.source == "resume"
        assert profile_skill.confidence == 0.9


def test_submit_interview_malformed_output_falls_back_without_touching_confidence(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        skill = _skill(session)
        interview = start_interview(session, profile.id, skill.id, FakeLLM("not json"))
        session.commit()

        result = submit_interview(session, profile.id, interview.id, ["a", "b"], FakeLLM("not json"))
        session.commit()
        assert result.verdict == "could_not_assess"
        assert result.evaluator == "deterministic-fallback"
        assert session.execute(select(ProfileSkill)).scalars().first() is None


def test_submit_interview_rejects_double_submission_and_wrong_answer_count(tmp_path):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        skill = _skill(session)
        interview = start_interview(session, profile.id, skill.id, FakeLLM("not json"))
        session.commit()

        eval_llm = FakeLLM(json.dumps({"verdict": "remediate", "feedback": "Some rust.", "resources": ["Docs page"]}))
        submit_interview(session, profile.id, interview.id, ["a", "b"], eval_llm)
        session.commit()

        try:
            submit_interview(session, profile.id, interview.id, ["a", "b"], eval_llm)
            assert False, "expected a ValueError for re-submission"
        except ValueError as exc:
            assert "already been evaluated" in str(exc)

        interview2 = start_interview(session, profile.id, skill.id, FakeLLM("not json"))
        session.commit()
        try:
            submit_interview(session, profile.id, interview2.id, ["only one"], eval_llm)
            assert False, "expected a ValueError for a mismatched answer count"
        except ValueError as exc:
            assert "answer is required" in str(exc)


def test_skill_check_api_round_trip_and_rebuild_offers_a_project(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        profile = get_or_create_profile(session, settings)
        skill = _skill(session, "Rust")
        skill_id = skill.id
        # profile_skill_page() only lists a skill that has a ProfileSkill or
        # JobSkill row -- give it one so /skills actually renders the card
        # the interview history assertion below checks.
        session.add(ProfileSkill(profile_id=profile.id, skill_id=skill_id, source="manual", confidence=0.5, evidence="Self-reported."))
        session.commit()

    responses = iter([
        json.dumps({"questions": ["Q1?", "Q2?"]}),
        json.dumps({"verdict": "rebuild", "feedback": "No real depth yet.", "resources": []}),
    ])

    class SequencedLLM:
        def complete_json(self, system, user):
            return next(responses)

    monkeypatch.setattr(app_module, "build_llm_client", lambda cfg: SequencedLLM())
    client = TestClient(create_app(settings))

    started = client.post(f"/api/skills/{skill_id}/interview/start")
    assert started.status_code == 201
    body = started.json()
    assert body["questions"] == ["Q1?", "Q2?"]

    submitted = client.post(
        f"/api/skills/{skill_id}/interview/{body['id']}/submit",
        json={"answers": ["not sure", "never used it"]},
    )
    assert submitted.status_code == 200
    result = submitted.json()
    assert result["verdict"] == "rebuild"

    skills_html = client.get("/skills").text
    assert "Past skill checks (1)" in skills_html
    assert "rebuild" in skills_html
