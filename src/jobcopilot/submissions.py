"""Validated, review-first coaching project submissions."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path, PurePosixPath
from typing import Iterable
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from .models import Project, ProjectSubmission, ProjectSubmissionKind, ProjectSubmissionStatus

MAX_RESPONSE_LENGTH = 100_000
MAX_FILES = 100
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_GITHUB_REFERENCE_LENGTH = 2048


def _project_for_profile(session: Session, project_id: int, profile_id: int) -> Project:
    project = session.get(Project, project_id)
    if project is None or project.profile_id != profile_id:
        raise ValueError("Coaching project not found.")
    return project


def _safe_name(name: str) -> str:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        raise ValueError("artifact filenames must be relative and stay within the submission folder.")
    return str(path)


def create_written_submission(
    session: Session, profile_id: int, project_id: int, content: str, title: str = ""
) -> ProjectSubmission:
    _project_for_profile(session, project_id, profile_id)
    content = content.strip()
    if not content:
        raise ValueError("written response cannot be empty.")
    if len(content) > MAX_RESPONSE_LENGTH:
        raise ValueError(f"written response cannot exceed {MAX_RESPONSE_LENGTH} characters.")
    submission = ProjectSubmission(
        project_id=project_id,
        kind=ProjectSubmissionKind.WRITTEN_RESPONSE,
        title=title.strip()[:200],
        content=content,
    )
    session.add(submission)
    session.flush()
    return submission


def create_local_submission(
    session: Session,
    profile_id: int,
    project_id: int,
    files: Iterable[tuple[str, bytes]],
    storage_root: Path,
    title: str = "",
) -> ProjectSubmission:
    _project_for_profile(session, project_id, profile_id)
    entries = list(files)
    if not entries or len(entries) > MAX_FILES:
        raise ValueError(f"local submission must contain between 1 and {MAX_FILES} files.")
    normalized: list[tuple[str, bytes]] = []
    for name, data in entries:
        safe_name = _safe_name(name)
        if len(data) > MAX_FILE_BYTES:
            raise ValueError(f"artifact {safe_name} exceeds the {MAX_FILE_BYTES} byte limit.")
        normalized.append((safe_name, data))

    submission = ProjectSubmission(
        project_id=project_id,
        kind=ProjectSubmissionKind.LOCAL_FILES,
        title=title.strip()[:200],
    )
    session.add(submission)
    session.flush()
    artifact_dir = storage_root / str(project_id) / str(submission.id)
    artifact_dir.mkdir(parents=True, exist_ok=False)
    manifest = []
    for name, data in normalized:
        destination = artifact_dir / Path(name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        manifest.append({"path": name, "bytes": len(data)})
    submission.artifact_dir = str(artifact_dir)
    submission.manifest_json = json.dumps(manifest)
    submission.metadata_json = json.dumps({"storage": "local", "file_count": len(manifest)})
    session.flush()
    return submission


def _normalize_github_reference(reference: str, ref: str = "") -> tuple[str, str, str]:
    """Validate a GitHub repository reference without resolving or fetching it."""
    raw_reference = str(reference or "").strip()
    raw_ref = str(ref or "").strip()
    if not raw_reference or len(raw_reference) > MAX_GITHUB_REFERENCE_LENGTH:
        raise ValueError("GitHub repository reference is required and must be reasonably sized.")
    if any(ord(char) < 32 or ord(char) == 127 for char in raw_reference + raw_ref):
        raise ValueError("GitHub repository references cannot contain control characters.")
    candidate = raw_reference if "://" in raw_reference else f"https://github.com/{raw_reference}"
    parsed = urlsplit(candidate)
    if (
        parsed.scheme.lower() != "https"
        or parsed.hostname is None
        or parsed.hostname.lower() != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("GitHub references must use an HTTPS github.com repository URL.")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2 or any(part in {".", ".."} for part in parts):
        raise ValueError("GitHub reference must identify one repository as owner/repository.")
    owner, repository = parts
    if repository.endswith(".git"):
        repository = repository[:-4]
    if (
        not owner
        or not repository
        or any(char in owner + repository for char in "\\<>:\"|?*%")
        or any(char.isspace() for char in owner + repository)
    ):
        raise ValueError("GitHub reference contains an invalid repository name.")
    ref_parts = raw_ref.split("/")
    if raw_ref and (
        len(raw_ref) > 512
        or "\\" in raw_ref
        or any(char.isspace() for char in raw_ref)
        or not all(part not in {"", ".", ".."} for part in ref_parts)
    ):
        raise ValueError("GitHub ref must be a single safe branch, tag, or commit reference.")
    normalized_ref = raw_ref
    repository_url = f"https://github.com/{owner}/{repository}"
    normalized = f"{repository_url}@{normalized_ref}" if normalized_ref else repository_url
    return normalized, repository_url, normalized_ref


def create_github_submission(
    session: Session,
    profile_id: int,
    project_id: int,
    reference: str,
    ref: str = "",
    title: str = "",
) -> ProjectSubmission:
    _project_for_profile(session, project_id, profile_id)
    normalized, repository_url, normalized_ref = _normalize_github_reference(reference, ref)
    submission = ProjectSubmission(
        project_id=project_id,
        kind=ProjectSubmissionKind.GITHUB_REPOSITORY,
        title=title.strip()[:200],
        content=normalized,
        metadata_json=json.dumps({
            "provider": "github",
            "repository_url": repository_url,
            "ref": normalized_ref,
            "fetched": False,
            "execution": False,
        }),
    )
    session.add(submission)
    session.flush()
    return submission


def submit_submission(session: Session, profile_id: int, submission_id: int) -> ProjectSubmission:
    submission = session.get(ProjectSubmission, submission_id)
    if submission is None or submission.project.profile_id != profile_id:
        raise ValueError("Submission not found.")
    if submission.status is not ProjectSubmissionStatus.DRAFT:
        raise ValueError("Only draft submissions can be submitted.")
    submission.status = ProjectSubmissionStatus.SUBMITTED
    submission.submitted_at = dt.datetime.utcnow()
    session.flush()
    return submission


def submission_status(submission: ProjectSubmission) -> dict:
    return {
        "id": submission.id,
        "project_id": submission.project_id,
        "kind": submission.kind.value,
        "title": submission.title,
        "content": submission.content,
        "status": submission.status.value,
        "artifact_dir": submission.artifact_dir,
        "manifest": json.loads(submission.manifest_json or "[]"),
        "metadata": json.loads(submission.metadata_json or "{}"),
        "created_at": submission.created_at.isoformat() if submission.created_at else None,
        "submitted_at": submission.submitted_at.isoformat() if submission.submitted_at else None,
        "evaluations": [
            {
                "id": evaluation.id,
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
            for evaluation in sorted(submission.evaluations, key=lambda item: item.attempt_number)
        ],
    }
