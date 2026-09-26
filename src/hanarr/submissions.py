"""Validated, review-first coaching project submissions."""
from __future__ import annotations

import json
import shutil
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable
from urllib.parse import urlsplit

import httpx
from sqlalchemy.orm import Session

from .models import Project, ProjectSubmission, ProjectSubmissionKind, ProjectSubmissionStatus, utc_now

MAX_RESPONSE_LENGTH = 100_000
MAX_FILES = 100
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_FILE_BYTES = 50 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 64 * 1024
MAX_GITHUB_REFERENCE_LENGTH = 2048

GITHUB_API_BASE = "https://api.github.com"
GITHUB_FETCH_TIMEOUT = 15.0
MAX_DIFF_FILES = 100
MAX_PATCH_CHARS = 4_000
MAX_TOTAL_DIFF_CHARS = 100_000


def _project_for_profile(session: Session, project_id: int, profile_id: int) -> Project:
    project = session.get(Project, project_id)
    if project is None or project.profile_id != profile_id:
        raise ValueError("Coaching project not found.")
    return project


def _safe_name(name: str) -> str:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts or "." in path.parts:
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
    files: Iterable[tuple[str, bytes | BinaryIO]],
    storage_root: Path,
    title: str = "",
) -> ProjectSubmission:
    _project_for_profile(session, project_id, profile_id)
    entries = list(files)
    if not entries or len(entries) > MAX_FILES:
        raise ValueError(f"local submission must contain between 1 and {MAX_FILES} files.")
    normalized: list[tuple[str, bytes | BinaryIO]] = []
    seen_names: set[str] = set()
    total_bytes = 0
    for name, data in entries:
        safe_name = _safe_name(name)
        if safe_name in seen_names:
            raise ValueError(f"artifact path {safe_name} is duplicated.")
        if isinstance(data, bytes):
            if len(data) > MAX_FILE_BYTES:
                raise ValueError(f"artifact {safe_name} exceeds the {MAX_FILE_BYTES} byte limit.")
            total_bytes += len(data)
            if total_bytes > MAX_TOTAL_FILE_BYTES:
                raise ValueError(f"local submission cannot exceed {MAX_TOTAL_FILE_BYTES} bytes in total.")
        seen_names.add(safe_name)
        normalized.append((safe_name, data))

    submission = ProjectSubmission(
        project_id=project_id,
        kind=ProjectSubmissionKind.LOCAL_FILES,
        title=title.strip()[:200],
    )
    storage_root = storage_root.resolve()
    storage_root.mkdir(parents=True, exist_ok=True)
    session.add(submission)
    session.flush()
    artifact_dir = (storage_root / str(project_id) / str(submission.id)).resolve()
    staging_dir = artifact_dir.with_name(f".{artifact_dir.name}.staging")
    if storage_root not in artifact_dir.parents or storage_root not in staging_dir.parents:
        raise ValueError("submission storage path is outside the configured storage root.")
    manifest = []
    try:
        staging_dir.mkdir(parents=True, exist_ok=False)
        for name, data in normalized:
            destination = (staging_dir / Path(name)).resolve()
            if staging_dir not in destination.parents:
                raise ValueError(f"artifact path {name} escapes the submission folder.")
            destination.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with destination.open("wb") as output:
                if isinstance(data, bytes):
                    output.write(data)
                    written = len(data)
                else:
                    while True:
                        chunk = data.read(UPLOAD_CHUNK_BYTES)
                        if not chunk:
                            break
                        written += len(chunk)
                        total_bytes += len(chunk)
                        if written > MAX_FILE_BYTES:
                            raise ValueError(f"artifact {name} exceeds the {MAX_FILE_BYTES} byte limit.")
                        if total_bytes > MAX_TOTAL_FILE_BYTES:
                            raise ValueError(f"local submission cannot exceed {MAX_TOTAL_FILE_BYTES} bytes in total.")
                        output.write(chunk)
            manifest.append({"path": name, "bytes": written})
        staging_dir.replace(artifact_dir)
        submission.artifact_dir = str(artifact_dir)
        submission.manifest_json = json.dumps(manifest)
        submission.metadata_json = json.dumps({"storage": "local", "file_count": len(manifest)})
        session.flush()
        return submission
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        session.delete(submission)
        session.flush()
        raise


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


def _github_api_get(client: httpx.Client, url: str) -> dict:
    response = client.get(url)
    if response.status_code == 404:
        raise ValueError("GitHub repository or reference was not found (or is private).")
    if response.status_code == 403:
        raise ValueError("GitHub API request was rate-limited or forbidden; try again later.")
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("GitHub API returned an unexpected response.")
    return data


def _build_diff_manifest(files: object) -> tuple[list[dict], bool]:
    """Turn GitHub's per-file commit diff into the same manifest shape local
    submissions already use (a list of {"path": ...} entries the evaluator
    reads), bounded so a large commit cannot pull an unbounded amount of
    diff text into the database."""
    if not isinstance(files, list):
        files = []
    truncated = len(files) > MAX_DIFF_FILES
    manifest: list[dict] = []
    total_patch_chars = 0
    for entry in files[:MAX_DIFF_FILES]:
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("filename", "")).strip()
        if not path:
            continue
        item = {
            "path": path,
            "status": str(entry.get("status") or "modified"),
            "additions": int(entry.get("additions") or 0),
            "deletions": int(entry.get("deletions") or 0),
        }
        patch = entry.get("patch")
        if isinstance(patch, str) and patch:
            remaining = MAX_TOTAL_DIFF_CHARS - total_patch_chars
            if remaining <= 0:
                item["patch_omitted"] = "total diff size limit reached"
            else:
                snippet = patch[: min(MAX_PATCH_CHARS, remaining)]
                if len(snippet) < len(patch):
                    snippet += "\n...[truncated]"
                item["patch"] = snippet
                total_patch_chars += len(snippet)
        manifest.append(item)
    return manifest, truncated


def fetch_github_submission(
    session: Session,
    profile_id: int,
    submission_id: int,
    client: httpx.Client | None = None,
) -> ProjectSubmission:
    """Explicit, opt-in, read-only fetch of the submitted repository's most
    recent commit at the referenced branch/tag/commit, for review before the
    submission is ever sent for evaluation.

    This only calls GitHub's public REST API over HTTPS (the same kind of
    public-API access the job connectors use) to read repository and commit
    metadata. It never runs `git`, never clones or checks out a working
    tree, and never executes anything from the repository — the resulting
    file-level diff is stored as plain text/counts, mirroring the manifest
    already produced for local file submissions.
    """
    submission = session.get(ProjectSubmission, submission_id)
    if submission is None or submission.project.profile_id != profile_id:
        raise ValueError("Submission not found.")
    if submission.kind is not ProjectSubmissionKind.GITHUB_REPOSITORY:
        raise ValueError("Only GitHub submissions can be fetched for review.")
    if submission.status is not ProjectSubmissionStatus.DRAFT:
        raise ValueError("Only a draft submission can be fetched for review.")

    metadata = json.loads(submission.metadata_json or "{}")
    repository_url = str(metadata.get("repository_url", ""))
    owner_repo = repository_url.removeprefix("https://github.com/").strip("/")
    if owner_repo.count("/") != 1:
        raise ValueError("Submission is missing a valid repository reference.")
    owner, repository = owner_repo.split("/", 1)
    ref = str(metadata.get("ref") or "")

    http_client = client or httpx.Client(
        timeout=GITHUB_FETCH_TIMEOUT,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "hanarr-coaching-review"},
    )
    try:
        try:
            repo = _github_api_get(http_client, f"{GITHUB_API_BASE}/repos/{owner}/{repository}")
            resolved_ref = ref or str(repo.get("default_branch") or "HEAD")
            commit = _github_api_get(
                http_client, f"{GITHUB_API_BASE}/repos/{owner}/{repository}/commits/{resolved_ref}"
            )
        except (httpx.HTTPError, OSError) as exc:
            raise ValueError(f"Could not reach GitHub to fetch this submission: {exc}") from exc
    finally:
        if client is None:
            http_client.close()

    manifest, truncated = _build_diff_manifest(commit.get("files"))
    commit_info = commit.get("commit") if isinstance(commit.get("commit"), dict) else {}
    author_info = commit_info.get("author") if isinstance(commit_info.get("author"), dict) else {}

    submission.manifest_json = json.dumps(manifest)
    metadata.update({
        "fetched": True,
        "fetched_at": utc_now().isoformat(),
        "resolved_ref": resolved_ref,
        "commit_sha": commit.get("sha"),
        "commit_message": str(commit_info.get("message", ""))[:500],
        "commit_author": str(author_info.get("name", ""))[:200],
        "commit_date": str(author_info.get("date", "")),
        "repository_description": str(repo.get("description") or "")[:500],
        "repository_default_branch": repo.get("default_branch", ""),
        "repository_private": bool(repo.get("private")),
        "files_changed": len(manifest),
        "files_truncated": truncated,
        "execution": False,
    })
    submission.metadata_json = json.dumps(metadata)
    session.flush()
    return submission


def submit_submission(session: Session, profile_id: int, submission_id: int) -> ProjectSubmission:
    submission = session.get(ProjectSubmission, submission_id)
    if submission is None or submission.project.profile_id != profile_id:
        raise ValueError("Submission not found.")
    if submission.status is not ProjectSubmissionStatus.DRAFT:
        raise ValueError("Only draft submissions can be submitted.")
    submission.status = ProjectSubmissionStatus.SUBMITTED
    submission.submitted_at = utc_now()
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
