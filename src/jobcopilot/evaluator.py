"""Review-first evaluation of submitted coaching project evidence."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .llm.base import LLMClient
from .models import Project, ProjectEvaluation, ProjectSubmission, ProjectSubmissionStatus

SYSTEM_PROMPT = """Evaluate a coaching project submission against its brief and rubric.
Return ONLY JSON:
{"outcome":"passed|needs_improvement|inconclusive","score":0,
"scores":{"criterion":0},"strengths":["..."],"improvements":["..."],
"actionable_feedback":["..."],"feedback":"..."}
Do not claim a skill is proven from a weak, missing, or ambiguous submission.
Use outcome=needs_improvement when required evidence is absent."""


def _brief_and_rubric(project: Project) -> tuple[dict[str, Any], list[str]]:
    brief = json.loads(project.brief_json or "{}")
    rubric = brief.get("rubric")
    if not isinstance(rubric, list) or not rubric:
        tasks = brief.get("tasks") or []
        rubric = [str(task.get("title", "")).strip() for task in tasks if isinstance(task, dict)]
    rubric = [item for item in rubric if isinstance(item, str) and item.strip()]
    return brief, rubric or ["Demonstrates the target outcome with concrete evidence"]


def _submission_text(submission: ProjectSubmission) -> str:
    manifest = json.loads(submission.manifest_json or "[]")
    paths = " ".join(item.get("path", "") for item in manifest if isinstance(item, dict))
    return " ".join(part for part in (submission.title, submission.content, paths) if part)


def _deterministic_result(project: Project, submission: ProjectSubmission) -> dict[str, Any]:
    _, rubric = _brief_and_rubric(project)
    text = _submission_text(submission).lower()
    evidence_terms = ("built", "implemented", "tested", "benchmark", "evidence", "result", "document")
    evidence_count = sum(term in text for term in evidence_terms)
    score = min(100.0, max(0.0, 35.0 + evidence_count * 10.0)) if text else 0.0
    strengths = ["Submission contains reviewable project evidence."] if text else []
    improvements = [] if score >= 70 else ["Add concrete implementation, validation, and outcome evidence."]
    actionable = [] if score >= 70 else [f"Address rubric criterion: {rubric[0]}."]
    outcome = "passed" if score >= 70 else "needs_improvement"
    return {
        "outcome": outcome,
        "score": score,
        "scores": {criterion: score for criterion in rubric},
        "strengths": strengths,
        "improvements": improvements,
        "actionable_feedback": actionable,
        "feedback": "Deterministic review completed; provide more evidence before retrying." if improvements else "Evidence meets the deterministic review threshold.",
    }


def _normalize(raw: str, fallback: dict[str, Any]) -> dict[str, Any]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("evaluation must be an object")
    outcome = str(value.get("outcome", "")).strip()
    if outcome not in {"passed", "needs_improvement", "inconclusive"}:
        raise ValueError("evaluation outcome is invalid")
    score = float(value.get("score"))
    if not 0 <= score <= 100:
        raise ValueError("evaluation score is outside 0-100")
    lists = {}
    for key in ("strengths", "improvements", "actionable_feedback"):
        item = value.get(key, [])
        if not isinstance(item, list):
            raise ValueError(f"{key} must be a list")
        lists[key] = [str(entry).strip() for entry in item if str(entry).strip()]
    scores = value.get("scores", {})
    if not isinstance(scores, dict):
        raise ValueError("scores must be an object")
    return {
        "outcome": outcome,
        "score": score,
        "scores": {str(key): float(val) for key, val in scores.items()},
        **lists,
        "feedback": str(value.get("feedback", "")).strip() or fallback["feedback"],
    }


def evaluate_submission(
    session: Session, profile_id: int, submission_id: int, llm: LLMClient
) -> ProjectEvaluation:
    submission = session.get(ProjectSubmission, submission_id)
    if submission is None or submission.project.profile_id != profile_id:
        raise ValueError("Submission not found.")
    if submission.status is not ProjectSubmissionStatus.SUBMITTED:
        raise ValueError("Only submitted evidence can be evaluated.")
    project = submission.project
    fallback = _deterministic_result(project, submission)
    evaluator = "deterministic"
    error = None
    try:
        brief, rubric = _brief_and_rubric(project)
        raw = llm.complete_json(
            SYSTEM_PROMPT,
            json.dumps({"brief": brief, "rubric": rubric, "submission": {
                "kind": submission.kind.value, "title": submission.title,
                "content": submission.content, "manifest": json.loads(submission.manifest_json or "[]"),
            }}),
        )
        result = _normalize(raw, fallback)
        evaluator = "llm"
    except Exception as exc:  # provider and malformed output both have a safe path
        result = fallback
        error = str(exc)[:500]
        evaluator = "deterministic-fallback"

    attempt = session.scalar(
        select(func.max(ProjectEvaluation.attempt_number)).where(
            ProjectEvaluation.submission_id == submission.id
        )
    ) or 0
    evaluation = ProjectEvaluation(
        submission_id=submission.id,
        attempt_number=attempt + 1,
        evaluator=evaluator,
        passed=result["outcome"] == "passed",
        score=result["score"],
        outcome=result["outcome"],
        scores_json=json.dumps(result["scores"]),
        strengths_json=json.dumps(result["strengths"]),
        improvements_json=json.dumps(result["improvements"]),
        actionable_feedback_json=json.dumps(result["actionable_feedback"]),
        feedback=result["feedback"],
        error=error,
    )
    session.add(evaluation)
    submission.status = ProjectSubmissionStatus.EVALUATED
    session.flush()
    return evaluation


def evaluation_status(evaluation: ProjectEvaluation) -> dict[str, Any]:
    return {
        "id": evaluation.id,
        "submission_id": evaluation.submission_id,
        "attempt_number": evaluation.attempt_number,
        "evaluator": evaluation.evaluator,
        "passed": evaluation.passed,
        "outcome": evaluation.outcome,
        "score": evaluation.score,
        "scores": json.loads(evaluation.scores_json or "{}"),
        "strengths": json.loads(evaluation.strengths_json or "[]"),
        "improvements": json.loads(evaluation.improvements_json or "[]"),
        "actionable_feedback": json.loads(evaluation.actionable_feedback_json or "[]"),
        "feedback": evaluation.feedback,
        "error": evaluation.error,
        "evaluated_at": evaluation.evaluated_at.isoformat() if evaluation.evaluated_at else None,
    }


def resubmit_submission(session: Session, profile_id: int, submission_id: int) -> ProjectSubmission:
    submission = session.get(ProjectSubmission, submission_id)
    if submission is None or submission.project.profile_id != profile_id:
        raise ValueError("Submission not found.")
    if submission.status is not ProjectSubmissionStatus.EVALUATED:
        raise ValueError("Only evaluated submissions can be resubmitted.")
    submission.status = ProjectSubmissionStatus.DRAFT
    submission.submitted_at = None
    session.flush()
    return submission
