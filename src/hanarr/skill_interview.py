"""Short, bounded Q&A that tests whether a claimed skill still holds up --
neither resume wording nor a self-reported number actually verifies that
someone can still produce the knowledge."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from .llm.base import LLMClient
from .models import ProfileSkill, Skill, SkillInterview, utc_now

SYSTEM_PROMPT_QUESTIONS = """Generate 2 to 4 short interview questions that test genuine
hands-on knowledge of one skill, calibrated to the person's claimed proficiency and any
existing evidence. Return ONLY JSON: {"questions": ["...", "..."]}
Ask about real usage, tradeoffs, and troubleshooting -- avoid generic trivia a search engine
could answer."""

SYSTEM_PROMPT_EVALUATE = """Evaluate free-text answers to skill-verification questions.
Return ONLY JSON: {"verdict":"solid|remediate|rebuild","feedback":"...","resources":["..."]}
solid: the claimed confidence is corroborated by the answers.
remediate: real experience is evident but the answers show memory decay/rust, not absent
skill -- resources should name 1 or 2 information sources (docs page, canonical article or
book, course) as a light refresher.
rebuild: either a greenfield skill with no real depth yet, or experience degraded badly
enough that a refresher would not be enough -- resources should usually be empty here.
Resources are plain-text suggestions only; never claim they were fetched or verified."""

# A fixed floor, not scaled by a score -- unlike the coaching-project evaluator, the LLM
# here returns a category (solid/remediate/rebuild), not a comparable 0-100 number.
SOLID_CONFIDENCE = 0.75


def _fallback_questions(skill_name: str) -> list[str]:
    return [
        f"Describe a specific, recent time you used {skill_name} to solve a real problem. "
        "What made it non-trivial?",
        f"What is a common mistake or pitfall with {skill_name}, and how do you avoid or "
        "recover from it?",
    ]


def start_interview(session: Session, profile_id: int, skill_id: int, llm: LLMClient) -> SkillInterview:
    skill = session.get(Skill, skill_id)
    if skill is None:
        raise ValueError("Skill not found.")
    profile_skill = session.query(ProfileSkill).filter_by(
        profile_id=profile_id, skill_id=skill_id
    ).first()
    prior = session.query(SkillInterview).filter_by(
        profile_id=profile_id, skill_id=skill_id
    ).order_by(SkillInterview.id.desc()).first()
    context = {
        "skill": skill.name,
        "claimed_proficiency": profile_skill.proficiency if profile_skill else None,
        "claimed_confidence": profile_skill.confidence if profile_skill else None,
        "existing_evidence": profile_skill.evidence if profile_skill else "",
        "prior_interview_verdict": prior.verdict if prior else None,
    }
    try:
        raw = llm.complete_json(SYSTEM_PROMPT_QUESTIONS, json.dumps(context))
        data = json.loads(raw)
        questions = [str(q).strip() for q in data.get("questions", []) if str(q).strip()]
        if not 2 <= len(questions) <= 4:
            raise ValueError("invalid question count")
        source = "llm"
    except Exception:
        questions = _fallback_questions(skill.name)
        source = "deterministic"
    interview = SkillInterview(
        profile_id=profile_id, skill_id=skill_id,
        questions_json=json.dumps(questions), evaluator=source,
    )
    session.add(interview)
    session.flush()
    return interview


def submit_interview(
    session: Session, profile_id: int, interview_id: int, answers: list[str], llm: LLMClient
) -> SkillInterview:
    interview = session.get(SkillInterview, interview_id)
    if interview is None or interview.profile_id != profile_id:
        raise ValueError("Interview not found.")
    if interview.verdict is not None:
        raise ValueError("This interview has already been evaluated.")
    questions = json.loads(interview.questions_json or "[]")
    if len(answers) != len(questions) or not any(answer.strip() for answer in answers):
        raise ValueError("An answer is required for each question.")
    skill = session.get(Skill, interview.skill_id)

    try:
        raw = llm.complete_json(
            SYSTEM_PROMPT_EVALUATE,
            json.dumps({"skill": skill.name, "qa": list(zip(questions, answers))}),
        )
        data = json.loads(raw)
        verdict = str(data.get("verdict", "")).strip()
        if verdict not in {"solid", "remediate", "rebuild"}:
            raise ValueError("invalid verdict")
        feedback = str(data.get("feedback", "")).strip()
        resources_raw = data.get("resources", [])
        if not isinstance(resources_raw, list):
            raise ValueError("resources must be a list")
        resources = [str(item).strip() for item in resources_raw if str(item).strip()]
        evaluator = "llm"
    except Exception:
        verdict = "could_not_assess"
        feedback = "Could not assess this automatically -- try submitting again."
        resources = []
        evaluator = "deterministic-fallback"

    interview.answers_json = json.dumps(answers)
    interview.answered_at = utc_now()
    interview.verdict = verdict
    interview.feedback = feedback
    interview.resources_json = json.dumps(resources)
    interview.evaluator = evaluator

    if verdict == "solid":
        note = f"Corroborated by a mini-interview: {feedback}".strip()
        profile_skill = session.query(ProfileSkill).filter_by(
            profile_id=profile_id, skill_id=skill.id
        ).first()
        if profile_skill is None:
            session.add(ProfileSkill(
                profile_id=profile_id, skill_id=skill.id,
                source="interview", confidence=SOLID_CONFIDENCE, evidence=note,
            ))
        elif profile_skill.source == "interview":
            profile_skill.confidence = max(profile_skill.confidence or 0.0, SOLID_CONFIDENCE)
            profile_skill.evidence = note
        elif SOLID_CONFIDENCE > (profile_skill.confidence or 0.0):
            # Never silently overwrite a higher-trust resume/manual/project source's
            # confidence downward -- only raise it, and say why in evidence text.
            profile_skill.confidence = SOLID_CONFIDENCE
            profile_skill.evidence = f"{profile_skill.evidence} {note}".strip()

    session.flush()
    return interview


def interview_status(interview: SkillInterview) -> dict[str, Any]:
    return {
        "id": interview.id,
        "skill_id": interview.skill_id,
        "questions": json.loads(interview.questions_json or "[]"),
        "answers": json.loads(interview.answers_json or "[]"),
        "verdict": interview.verdict,
        "feedback": interview.feedback,
        "resources": json.loads(interview.resources_json or "[]"),
        "evaluator": interview.evaluator,
        "created_at": interview.created_at.isoformat() if interview.created_at else None,
        "answered_at": interview.answered_at.isoformat() if interview.answered_at else None,
    }


def interviews_for_skill(session: Session, profile_id: int, skill_id: int) -> list[dict[str, Any]]:
    rows = session.query(SkillInterview).filter_by(
        profile_id=profile_id, skill_id=skill_id
    ).order_by(SkillInterview.id.desc()).all()
    return [interview_status(row) for row in rows]
