"""Behavioral ("tell me about a time you...") interview prep: candidate questions
generated from the resume's actual work history, and a lightweight STAR
(Situation/Task/Action/Result) story builder that reviews wording without ever
inventing an achievement the person didn't state."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from .llm.base import LLMClient
from .models import JobPosting, Profile, StarQuestion, StarStory

SYSTEM_PROMPT_QUESTIONS = """Generate up to 6 behavioral interview questions ("tell me about
a time you...") based on this candidate's work history. Return ONLY JSON:
{"questions": [{"question": "...", "competency": "..."}]}
competency is one short label such as leadership, conflict, failure, ambiguity, technical
tradeoff, or cross-team collaboration. Base questions on the candidate's actual
titles/seniority/industries (and the job's stated requirements, when given) -- don't invent
specific achievements or projects the candidate didn't state."""

SYSTEM_PROMPT_REVIEW = """Review a draft STAR (Situation/Task/Action/Result) interview story.
Return ONLY JSON:
{"feedback":"...","tightened":{"situation":"...","task":"...","action":"...","result":"..."}}
Note in "feedback" if any component is vague, or if Result has no concrete/measurable
outcome. In "tightened", lightly tighten each field's wording for clarity and concision --
never invent a fact, number, or achievement the person didn't state. If a field is already
fine or empty, return it unchanged."""


def _fallback_questions() -> list[dict[str, str]]:
    return [
        {"question": "Tell me about a time you had to lead a project or initiative without formal authority.", "competency": "leadership"},
        {"question": "Describe a time you disagreed with a teammate or manager. How did you handle it?", "competency": "conflict"},
        {"question": "Tell me about a project that fell short of expectations. What did you learn?", "competency": "failure"},
        {"question": "Describe a time you had to make a decision with incomplete information.", "competency": "ambiguity"},
        {"question": "Tell me about a time you had to choose between two reasonable technical approaches.", "competency": "technical tradeoff"},
        {"question": "Describe a time you had to get buy-in from a team you didn't manage.", "competency": "cross-team collaboration"},
    ]


def generate_star_questions(
    session: Session, profile: Profile, llm: LLMClient, *, job_id: int | None = None
) -> list[StarQuestion]:
    summary = json.loads(profile.resume_summary_json or "{}")
    context: dict[str, Any] = {
        "titles": summary.get("titles") or [],
        "seniority": summary.get("seniority"),
        "industries": summary.get("industries") or [],
    }
    job = None
    if job_id is not None:
        job = session.get(JobPosting, job_id)
        if job is None or job.profile_id != profile.id:
            raise ValueError("Saved job not found.")
        context["job_title"] = job.title
        context["job_description"] = (job.description or "")[:4000]

    try:
        raw = llm.complete_json(SYSTEM_PROMPT_QUESTIONS, json.dumps(context))
        data = json.loads(raw)
        items = data.get("questions", [])
        if not isinstance(items, list) or not items:
            raise ValueError("no questions returned")
        parsed = []
        for item in items:
            if not isinstance(item, dict):
                continue
            question = str(item.get("question", "")).strip()
            competency = str(item.get("competency", "")).strip() or "general"
            if question:
                parsed.append({"question": question, "competency": competency})
        if not parsed:
            raise ValueError("no usable questions")
        # A deterministic fallback can't credibly weight toward a specific
        # job's requirements, so it's always tagged generic even when a
        # job_id was requested.
        source = "job_specific" if job is not None else "generic"
    except Exception:
        parsed = _fallback_questions()
        source = "generic"
        job = None

    created = []
    for item in parsed:
        row = StarQuestion(
            profile_id=profile.id, question=item["question"], competency=item["competency"],
            source=source, job_id=job.id if job is not None else None,
        )
        session.add(row)
        created.append(row)
    session.flush()
    return created


def get_or_create_story(session: Session, profile_id: int, question_id: int) -> StarStory:
    question = session.get(StarQuestion, question_id)
    if question is None or question.profile_id != profile_id:
        raise ValueError("Question not found.")
    story = session.query(StarStory).filter_by(question_id=question_id).first()
    if story is None:
        story = StarStory(question_id=question_id)
        session.add(story)
        session.flush()
    return story


def save_story_draft(
    session: Session, profile_id: int, story_id: int,
    situation: str, task: str, action: str, result: str,
) -> StarStory:
    story = session.get(StarStory, story_id)
    if story is None or story.question.profile_id != profile_id:
        raise ValueError("Story not found.")
    story.situation = situation
    story.task = task
    story.action = action
    story.result = result
    session.flush()
    return story


def review_story(session: Session, profile_id: int, story_id: int, llm: LLMClient) -> StarStory:
    story = session.get(StarStory, story_id)
    if story is None or story.question.profile_id != profile_id:
        raise ValueError("Story not found.")
    if not any(field.strip() for field in (story.situation, story.task, story.action, story.result)):
        raise ValueError("Write at least one STAR component before requesting a review.")

    try:
        raw = llm.complete_json(
            SYSTEM_PROMPT_REVIEW,
            json.dumps({
                "situation": story.situation, "task": story.task,
                "action": story.action, "result": story.result,
            }),
        )
        data = json.loads(raw)
        feedback = str(data.get("feedback", "")).strip()
        tightened_raw = data.get("tightened", {})
        if not isinstance(tightened_raw, dict):
            raise ValueError("tightened must be an object")
        tightened = {
            field: str(tightened_raw.get(field, "")).strip()
            for field in ("situation", "task", "action", "result")
        }
        story.evaluator = "llm"
    except Exception:
        feedback = "Could not review this automatically -- keep writing and try again later."
        tightened = {
            "situation": story.situation, "task": story.task,
            "action": story.action, "result": story.result,
        }
        story.evaluator = "deterministic-fallback"

    story.feedback = feedback
    story.tightened_json = json.dumps(tightened)
    session.flush()
    return story


def set_story_status(session: Session, profile_id: int, story_id: int, status: str) -> StarStory:
    if status not in {"draft", "complete"}:
        raise ValueError("status must be draft or complete.")
    story = session.get(StarStory, story_id)
    if story is None or story.question.profile_id != profile_id:
        raise ValueError("Story not found.")
    story.status = status
    session.flush()
    return story


def _story_dict(story: StarStory) -> dict[str, Any]:
    return {
        "id": story.id,
        "situation": story.situation,
        "task": story.task,
        "action": story.action,
        "result": story.result,
        "feedback": story.feedback,
        "tightened": json.loads(story.tightened_json or "{}"),
        "evaluator": story.evaluator,
        "status": story.status,
        "updated_at": story.updated_at.isoformat() if story.updated_at else None,
    }


def question_dict(question: StarQuestion, story: StarStory | None) -> dict[str, Any]:
    return {
        "id": question.id,
        "question": question.question,
        "competency": question.competency,
        "source": question.source,
        "job_id": question.job_id,
        "job": (
            {"id": question.job.id, "title": question.job.title, "company": question.job.company}
            if question.job is not None else None
        ),
        "story": _story_dict(story) if story is not None else None,
    }


def prep_page_status(session: Session, profile: Profile) -> dict[str, Any]:
    questions = session.query(StarQuestion).filter_by(
        profile_id=profile.id
    ).order_by(StarQuestion.id).all()
    stories_by_question = {
        story.question_id: story
        for story in session.query(StarStory)
        .join(StarQuestion, StarQuestion.id == StarStory.question_id)
        .filter(StarQuestion.profile_id == profile.id)
        .all()
    }
    grouped: dict[str, list[dict]] = {}
    for question in questions:
        bucket = question.competency or "general"
        grouped.setdefault(bucket, []).append(
            question_dict(question, stories_by_question.get(question.id))
        )
    return {
        "competencies": [{"name": name, "questions": items} for name, items in grouped.items()],
        "has_questions": bool(questions),
    }
