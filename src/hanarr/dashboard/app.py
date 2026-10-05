"""Minimal read/write dashboard: view matched jobs and reminders, update
a job's status, and trigger a search or reminder check on demand.
Deliberately server-rendered (Jinja2, a few forms) rather than a JS
framework — keeps the project approachable to contribute to and easy to
self-host with nothing but `hanarr serve`.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import tzlocal
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from ..config import DEFAULT_CONFIG_PATH, Preferences, Settings, backup_profile_preferences, effective_preferences
from ..agent_orchestration import AgentOrchestrator
from ..connectors.base import RawJobPosting, to_naive_utc
from ..db import get_active_profile, get_or_create_profile, list_profiles, make_session_factory
from ..llm import build_llm_client
from ..llm.base import NullLLMClient
from ..matching import COMMON_DEALBREAKERS, LLMScoringFailedError, score_fit
from ..ollama_setup import (
    SetupCancelled,
    SetupError,
    detect_ollama,
    installer_offer,
    pull_model,
    recommend_model_for_role,
    stage_ollama_installer,
)
from ..calendar_export import reminders_to_ics
from ..coaching_projects import create_coaching_project, project_status
from ..cover_letter import generate_and_store_cover_letter
from ..outreach import generate_and_store_outreach_email
from ..evaluator import evaluate_submission, resubmit_submission
from ..models import (
    ApplicationStatus,
    ChatMessage,
    ChatRole,
    JobPosting,
    JobSkill,
    Profile,
    Project,
    ProjectMode,
    ProjectSubmission,
    ProjectStatus,
    ProjectTask,
    ProjectTaskStatus,
    ProfileSkill,
    Reminder,
    ResumeProposal,
    ResumeVersion,
    ProvenSkill,
    ScoreSnapshot,
    SeenPosting,
    Skill,
    SkillGapStatus,
    SkillInterview,
    utc_now,
)
from ..pipeline import check_llm_available, rescore_all_jobs, run_search_cycle
from ..reminders import deliver_reminders, get_due_reminders, mark_completed
from ..search_state import (
    log_event as search_log_event,
    new_rescore_state,
    new_search_state,
    on_progress as search_on_progress,
    rescore_on_progress,
    reset_for_rescore,
    reset_for_run,
)
from .. import self_update
from .. import tutorials
from ..coach import CoachError, ask_coach, confirm_action, decline_action, recent_messages
from .. import __version__ as APP_VERSION
from ..update_state import new_update_state
from ..resume import (
    ALLOWED_RESUME_EXTENSIONS,
    MAX_RESUME_BYTES,
    parse_and_store_resume,
    retry_resume_extraction,
    suggest_boost_keywords,
)
from ..resume_loop import (
    approve_resume_proposal,
    create_resume_proposal,
    reject_resume_proposal,
    resume_status as resume_page_status,
)
from ..skill_analysis import (
    analyze_job,
    coaching_suggestions,
    market_demand_summary,
    profile_skill_page,
    saved_job_gap,
    saved_job_gaps,
)
from ..skill_interview import interview_status, interviews_for_skill, start_interview, submit_interview
from ..star_stories import (
    generate_star_questions,
    get_or_create_story,
    prep_page_status,
    question_dict,
    review_story,
    save_story_draft,
    set_story_status,
)
from ..submissions import (
    create_github_submission,
    create_local_submission,
    create_written_submission,
    fetch_github_submission,
    submit_submission,
    submission_status,
    UPLOAD_CHUNK_BYTES,
    MAX_FILES,
)
from .config_form import (
    apply_app_config_form,
    apply_preferences_form,
    apply_schedule_reminders_form,
    apply_updates_form,
    custom_dealbreakers,
    persist_secrets_from_form,
    save_settings_to_yaml,
    settings_to_dict,
    validate_and_build,
)
from ..update_service import UpdateCheckError, check_for_update

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"

# No login/auth -- this is still a local, single-operator instance. Multiple
# profiles are local "hats" on the same machine (each with their own resume,
# jobs, applications, skills, coaching projects), selected in the browser via
# a plain cookie rather than any server-side session. Search preferences
# (target titles, locations, connectors, LLM, schedule) stay global/shared
# across all profiles -- only per-person data is actually isolated.
PROFILE_COOKIE = "hanarr_profile_id"


def _active_profile_id(request: Request) -> int | None:
    raw = request.cookies.get(PROFILE_COOKIE)
    return int(raw) if raw and raw.isdigit() else None

# A wedged LLM call (Ollama hung, unreachable, or just very slow on a long
# prompt) would otherwise leave a background task's "running" flag stuck
# true forever, locking out all future attempts with no recovery short of
# restarting the server. Any task whose "running" flag has been set longer
# than the configured LLM timeout plus this buffer is treated as dead — the
# ceiling is generous on purpose, since a false "not stuck" verdict just
# means a redundant background thread, while a false "stuck" verdict would
# let two re-parses race on the same profile.
STUCK_TASK_BUFFER_SECONDS = 30.0

# Model downloads are unrelated to the LLM call timeout -- a multi-gigabyte
# pull on a slow connection can legitimately take far longer than any
# sensible inference timeout. Generous on purpose for the same reason
# STUCK_TASK_BUFFER_SECONDS is: a false "not stuck" verdict just means a
# redundant background thread, a false "stuck" verdict would let two pulls
# race on the same shared state.
MODEL_PULL_STUCK_SECONDS = 3600.0

# How long an "active" coaching project can go with no task-status change and
# no new submission before the Coaching page nudges it as stale. A fixed
# threshold, not one relative to the project's own task cadence -- most
# projects only ever have 3-5 tasks, too few to derive a meaningful per-project
# cadence from.
STALE_PROJECT_DAYS = 14


def _project_last_activity(project: Project) -> dt.datetime:
    stamps = [project.opted_in_at]
    for task in project.tasks:
        if task.updated_at is not None:
            stamps.append(task.updated_at)
    for submission in project.submissions:
        stamps.append(submission.created_at)
        if submission.submitted_at is not None:
            stamps.append(submission.submitted_at)
    return max(stamps)


def _project_is_stale(project: Project, now: dt.datetime) -> bool:
    if project.status is not ProjectStatus.ACTIVE:
        return False
    return (now - _project_last_activity(project)) > dt.timedelta(days=STALE_PROJECT_DAYS)


def task_is_stuck(task_state: dict, llm_timeout_seconds: float, now: float | None = None) -> bool:
    if not task_state["running"] or task_state["started_at"] is None:
        return False
    ceiling = llm_timeout_seconds + STUCK_TASK_BUFFER_SECONDS
    return ((now if now is not None else time.time()) - task_state["started_at"]) > ceiling


def format_posting_age(posted_at: dt.datetime | None, now: dt.datetime | None = None) -> str | None:
    """Renders posted_at as a short relative label for the dashboard, or
    None if there's nothing to show. now is naive UTC, matching how
    connectors normalize posted_at (see connectors.base.to_naive_utc)."""
    if posted_at is None:
        return None
    now = now if now is not None else utc_now()
    delta_seconds = (now - posted_at).total_seconds()
    if delta_seconds < 0:
        return "Posted today"  # clock skew between sources; don't show a negative age
    days = int(delta_seconds // 86400)
    if days == 0:
        return "Posted today"
    if days == 1:
        return "Posted yesterday"
    if days < 30:
        return f"Posted {days}d ago"
    months = days // 30
    return f"Posted {months}mo ago"


def is_recent_posting(posted_at: dt.datetime | None, now: dt.datetime | None = None, within_days: int = 3) -> bool:
    """True if posted_at is within the last `within_days` days — drives the
    "New" badge. Unknown posted_at (source doesn't provide one) is never
    flagged as new, since there's no evidence either way."""
    if posted_at is None:
        return False
    now = now if now is not None else utc_now()
    delta_seconds = (now - posted_at).total_seconds()
    return 0 <= delta_seconds < within_days * 86400


def create_app(
    settings: Settings, scheduler: Any = None, search_state: dict | None = None, update_state: dict | None = None,
) -> FastAPI:
    """`scheduler` is the BackgroundScheduler from start_scheduler(), passed
    through so the restart route can shut it down cleanly before
    re-executing the process. Optional — tests and other callers that don't
    run the scheduler can omit it; the restart route just skips that step.

    `search_state`/`update_state` are the same shared dicts passed to
    start_scheduler(), so a scheduled background search or a found/staged
    update shows up here identically to something triggered manually --
    pass the same objects to both, or omit either entirely (tests, or any
    caller that doesn't run a scheduler) and a fresh, unshared one is
    created."""
    app = FastAPI(title="Hanarr")

    @app.exception_handler(Exception)
    async def _log_unhandled_exception(request: Request, exc: Exception):
        # The packaged desktop build runs --windowed (no console -- see
        # scripts/build_windows.ps1), so stderr goes nowhere: without this,
        # an unhandled exception here left a user with nothing to report
        # beyond "Internal Server Error" and no way to diagnose it. This
        # guarantees a full traceback lands in the rotating log file
        # logging_setup.configure_file_logging() sets up, regardless of
        # uvicorn's own (log_level="warning") exception logging.
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return PlainTextResponse("Internal Server Error", status_code=500)

    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.cache = None
    templates.env.filters["posting_age"] = format_posting_age
    templates.env.filters["is_recent_posting"] = is_recent_posting
    # A Jinja global rather than per-route context so every page -- not just
    # the ones that remembered to pass it -- can show what version is
    # actually running, without a shared nav partial to hang it on.
    templates.env.globals["app_version"] = APP_VERSION
    session_factory = make_session_factory(settings)
    orchestrator = AgentOrchestrator(settings, client_builder=build_llm_client)
    market_analysis_llm = orchestrator.client_for("market_analysis")
    profiler_llm = orchestrator.client_for("profiler")
    curriculum_llm = orchestrator.client_for("curriculum")
    evaluator_llm = orchestrator.client_for("evaluator")
    resume_writer_llm = orchestrator.client_for("resume_writer")
    coach_llm = orchestrator.client_for("coach")

    # Shared with the background scheduler (see search_state.py) so a
    # scheduled run shows up here identically to a manual one, instead of
    # happening invisibly. `run_id` lets the browser tell "still the same
    # run" apart from "a new one started" across polls.
    state = search_state if search_state is not None else new_search_state()
    upd_state = update_state if update_state is not None else new_update_state()
    stop_event = threading.Event()
    # Lets the "Resume now" button cut a mid-search LLM-unavailable pause
    # short instead of waiting out the full retry interval -- see
    # pipeline._score_with_pause.
    resume_now_event = threading.Event()

    # Incremented on every successful config.yaml save. A page's rendered
    # config_version travels with any save it makes (a hidden field, or the
    # autosave script's tracked value); a mismatch means settings changed
    # via some other path since this page was loaded (another tab, a
    # resume upload's auto-populated preferences, etc.) -- see
    # _handle_config_post. Without this, a stale tab silently overwrites
    # whatever changed in the meantime the next time *any* field in it is
    # touched, which is exactly what caused a real data-loss incident
    # (locations, salary floor, and every job-source board wiped back to
    # blank/defaults).
    config_version = {"value": 0}

    def _log_event(entry: dict) -> None:
        search_log_event(state, entry)

    def _on_progress(event: dict) -> None:
        search_on_progress(state, event)

    def _run_search_in_background(run_id: int, profile_id: int | None = None):
        reset_for_run(state, run_id, "manual")
        stop_event.clear()
        resume_now_event.clear()
        try:
            from ..connectors import build_enabled_connectors

            with session_factory() as session:
                profile = get_active_profile(session, settings, profile_id)
                resume_summary = json.loads(profile.resume_summary_json or "{}")
                state["sources_total"] = len(build_enabled_connectors(
                    settings.sources, resume_summary=resume_summary,
                    preferences=effective_preferences(profile, settings),
                ))
                n = run_search_cycle(
                    session, settings, profile, market_analysis_llm,
                    on_progress=_on_progress,
                    should_stop=stop_event.is_set,
                    resume_event=resume_now_event,
                )
                profile.last_search_at = utc_now()
                profile.last_search_new_count = n
                profile.last_search_trigger = "manual"
                session.commit()
                if stop_event.is_set():
                    state["last_search_result"] = f"Search stopped — {n} new posting(s) kept."
                else:
                    state["last_search_result"] = f"{n} new posting(s) matched and stored."
        except Exception as exc:  # noqa: BLE001
            logger.exception("Manual search cycle failed")
            # Show the actual error rather than a generic "check the logs" --
            # a bare guess (e.g. "is the LLM reachable?") can point at the
            # wrong thing entirely, as it did for an LLM that was reachable
            # but didn't have the configured model pulled (a 404, not a
            # connection failure). Bounded so a very long exception message
            # doesn't take over the status line.
            message = f"Search failed — {exc}"
            if len(message) > 300:
                message = message[:300] + "…"
            state["last_search_result"] = message
            _log_event({"kind": "error", "text": message})
        finally:
            state["search_running"] = False
            stop_event.clear()

    # "Rescore all jobs" -- re-scores every saved posting against the
    # current resume/preferences/LLM, same background-thread-plus-polling
    # shape as a search. Its own state/stop-event: independent of a live
    # search (either could be running without blocking the other, though
    # in practice a user is unlikely to kick off both at once).
    rescore_state = new_rescore_state()
    rescore_stop_event = threading.Event()
    rescore_resume_now_event = threading.Event()

    def _rescore_log_event(entry: dict) -> None:
        search_log_event(rescore_state, entry)

    def _rescore_on_progress(event: dict) -> None:
        rescore_on_progress(rescore_state, event)

    def _run_rescore_in_background(run_id: int, profile_id: int | None = None):
        rescore_stop_event.clear()
        rescore_resume_now_event.clear()
        try:
            with session_factory() as session:
                profile = get_active_profile(session, settings, profile_id)
                total = session.scalar(
                    select(func.count()).select_from(JobPosting).where(JobPosting.profile_id == profile.id)
                ) or 0
                reset_for_rescore(rescore_state, run_id, total)
                n = rescore_all_jobs(
                    session, settings, profile, market_analysis_llm,
                    on_progress=_rescore_on_progress,
                    should_stop=rescore_stop_event.is_set,
                    resume_event=rescore_resume_now_event,
                )
                if rescore_stop_event.is_set():
                    rescore_state["last_result"] = f"Rescore stopped — {n} job(s) rescored."
                else:
                    rescore_state["last_result"] = f"{n} job(s) rescored."
        except Exception as exc:  # noqa: BLE001
            logger.exception("Rescore-all-jobs cycle failed")
            message = f"Rescore failed — {exc}"
            if len(message) > 300:
                message = message[:300] + "…"
            rescore_state["last_result"] = message
            _rescore_log_event({"kind": "error", "text": message})
        finally:
            rescore_state["running"] = False
            rescore_stop_event.clear()

    def _task_is_stuck(task_state: dict) -> bool:
        return task_is_stuck(task_state, settings.llm.timeout_seconds)

    # Same pattern as the search trigger: one LLM call, run in a background
    # thread so the request returns immediately, with a run_id so the
    # browser can tell "still this run" apart from a new click.
    keyword_state = {
        "running": False,
        "run_id": 0,
        "keywords": None,
        "error": None,
        "started_at": None,
    }

    def _suggest_keywords_in_background(run_id: int, profile_id: int | None = None):
        keyword_state["running"] = True
        keyword_state["run_id"] = run_id
        keyword_state["keywords"] = None
        keyword_state["error"] = None
        keyword_state["started_at"] = time.time()
        try:
            with session_factory() as session:
                profile = get_active_profile(session, settings, profile_id)
                if not profile.resume_text:
                    keyword_state["error"] = "No resume text on file — run `hanarr init` first."
                    return
                keywords = suggest_boost_keywords(
                    profile.resume_text, effective_preferences(profile, settings).target_titles, profiler_llm
                )
                if not keywords:
                    keyword_state["error"] = "The model didn't return any keywords — try again."
                else:
                    keyword_state["keywords"] = keywords
        except Exception as exc:  # noqa: BLE001
            logger.exception("Keyword suggestion failed")
            # Show the actual error instead of guessing "is the LLM
            # reachable?" -- that guess is actively misleading when the
            # real cause is something else entirely, e.g. Ollama running
            # fine but returning 404 because the configured model was
            # never pulled.
            message = f"Suggestion failed — {exc}"
            keyword_state["error"] = message if len(message) <= 300 else message[:300] + "…"
        finally:
            keyword_state["running"] = False

    # Same background-thread pattern again: saving the upload is instant,
    # but the re-parse is an LLM call.
    RESUMES_DIR = Path(settings.profile.resume_path).parent
    resume_state = {
        "running": False,
        "run_id": 0,
        "result": None,
        "error": None,
        "started_at": None,
    }

    def _reparse_resume_in_background(
        run_id: int,
        original_filename: str | None = None,
        profile_id: int | None = None,
        resume_path: Path | None = None,
        persist_global_path: bool = True,
    ):
        resume_state["running"] = True
        resume_state["run_id"] = run_id
        resume_state["result"] = None
        resume_state["error"] = None
        resume_state["started_at"] = time.time()
        try:
            with session_factory() as session:
                profile, summary, prefs_changed = parse_and_store_resume(
                    session, settings, profiler_llm, original_filename=original_filename,
                    profile_id=profile_id, resume_path=resume_path,
                )

                # A previous run may have been declared stuck and superseded
                # by a newer upload while this one was still blocked on the
                # LLM call. If so, don't let this stale run's save clobber
                # whatever the newer run has already written.
                if resume_state["run_id"] != run_id:
                    logger.warning(
                        "Resume re-parse run %d finished after being superseded by run %d; discarding its save.",
                        run_id, resume_state["run_id"],
                    )
                    return

                # Only the default profile's upload updates the shared
                # config.yaml path -- a secondary profile's resume lives at
                # its own per-profile path (see upload_resume) and would
                # otherwise silently become everyone's global default.
                if persist_global_path:
                    save_settings_to_yaml(settings, str(DEFAULT_CONFIG_PATH))
                if summary.get("_extraction_error"):
                    resume_state["error"] = (
                        f"Resume saved, but structured extraction failed "
                        f"({summary['_extraction_error']}); falling back to raw-text matching."
                    )
                else:
                    titles = ", ".join(summary.get("titles") or []) or "none detected"
                    skills = ", ".join((summary.get("skills") or [])[:6]) or "none detected"
                    note = " Preferences updated from the new resume." if prefs_changed else ""
                    resume_state["result"] = f"Detected titles: {titles}. Top skills: {skills}.{note}"
        except Exception as e:  # noqa: BLE001
            logger.exception("Resume re-parse failed")
            if resume_state["run_id"] == run_id:
                resume_state["error"] = (
                    f"Couldn't read the uploaded file ({e}) — make sure it's a valid PDF, "
                    f"or upload a .txt/.md instead."
                )
        finally:
            if resume_state["run_id"] == run_id:
                resume_state["running"] = False

    def _retry_resume_extraction_in_background(run_id: int, profile_id: int | None = None):
        """Same shared resume_state/thread pattern as an upload's re-parse,
        but re-runs extraction on the already-stored raw text instead of
        re-reading a file -- for retrying after a first parse failed (e.g.
        no LLM was available yet) without needing the file again."""
        resume_state["running"] = True
        resume_state["run_id"] = run_id
        resume_state["result"] = None
        resume_state["error"] = None
        resume_state["started_at"] = time.time()
        try:
            with session_factory() as session:
                profile, summary, prefs_changed = retry_resume_extraction(
                    session, settings, profiler_llm, profile_id=profile_id,
                )
                if resume_state["run_id"] != run_id:
                    return
                if summary.get("_extraction_error"):
                    resume_state["error"] = (
                        f"Extraction failed again ({summary['_extraction_error']}); "
                        f"still falling back to raw-text matching."
                    )
                else:
                    titles = ", ".join(summary.get("titles") or []) or "none detected"
                    skills = ", ".join((summary.get("skills") or [])[:6]) or "none detected"
                    note = " Preferences updated from the resume." if prefs_changed else ""
                    resume_state["result"] = f"Detected titles: {titles}. Top skills: {skills}.{note}"
        except Exception as e:  # noqa: BLE001
            logger.exception("Resume extraction retry failed")
            if resume_state["run_id"] == run_id:
                resume_state["error"] = f"Retry failed — {e}"
        finally:
            if resume_state["run_id"] == run_id:
                resume_state["running"] = False

    # Same background-thread pattern again: model downloads can take
    # minutes, so this runs off-request with a poll endpoint reporting
    # live progress, instead of the request blocking until the whole
    # multi-gigabyte pull finishes.
    pull_state = {
        "running": False,
        "run_id": 0,
        "model": None,
        "phase": None,
        "percent": None,
        "error": None,
        "started_at": None,
    }
    pull_cancel_event = threading.Event()

    def _pull_model_in_background(run_id: int, model: str):
        pull_state["running"] = True
        pull_state["run_id"] = run_id
        pull_state["model"] = model
        pull_state["phase"] = "starting"
        pull_state["percent"] = None
        pull_state["error"] = None
        pull_state["started_at"] = time.time()
        pull_cancel_event.clear()

        def on_progress(event: dict):
            if pull_state["run_id"] != run_id:
                return
            status = event.get("status")
            if status:
                pull_state["phase"] = status
            completed, total = event.get("completed"), event.get("total")
            if isinstance(completed, (int, float)) and isinstance(total, (int, float)) and total > 0:
                pull_state["percent"] = round(completed / total * 100, 1)

        try:
            pull_model(
                model, settings.llm.base_url, consent=True,
                cancel_event=pull_cancel_event, on_progress=on_progress,
            )
            if pull_state["run_id"] == run_id:
                pull_state["phase"] = "success"
                pull_state["percent"] = 100.0
        except SetupCancelled:
            if pull_state["run_id"] == run_id:
                pull_state["phase"] = "cancelled"
        except SetupError as exc:
            logger.warning("Model pull failed for %r: %s", model, exc)
            if pull_state["run_id"] == run_id:
                pull_state["error"] = str(exc)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Model pull failed unexpectedly for %r", model)
            if pull_state["run_id"] == run_id:
                pull_state["error"] = f"Download failed: {exc}"
        finally:
            if pull_state["run_id"] == run_id:
                pull_state["running"] = False

    @app.get("/profiles")
    def profiles_page(request: Request):
        with session_factory() as session:
            profiles = list_profiles(session)
            active_id = get_active_profile(session, settings, _active_profile_id(request)).id
            return templates.TemplateResponse(
                request=request,
                name="profiles.html",
                context={
                    "profiles": [{"id": p.id, "name": p.name} for p in profiles],
                    "active_profile_id": active_id,
                },
            )

    @app.post("/profiles")
    def create_profile(name: str = Form(...)):
        name = name.strip()
        if not name:
            return RedirectResponse("/profiles", status_code=303)
        with session_factory() as session:
            # A new profile is likely a different person entirely, not a
            # continuation of whoever set up this instance -- it starts
            # from bare preferences defaults (blank target titles/
            # locations, generic seniority/employment-type defaults), not
            # a copy of the existing shared config.yaml values, which
            # could be highly specific to someone else's job search.
            profile = Profile(name=name, preferences_json=Preferences().model_dump_json())
            session.add(profile)
            session.commit()
            session.refresh(profile)
            new_id = profile.id
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(PROFILE_COOKIE, str(new_id), max_age=60 * 60 * 24 * 365, samesite="lax")
        return response

    @app.post("/profiles/{profile_id}/activate")
    def activate_profile(profile_id: int):
        with session_factory() as session:
            if session.get(Profile, profile_id) is None:
                return JSONResponse({"error": "Profile not found."}, status_code=404)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(PROFILE_COOKIE, str(profile_id), max_age=60 * 60 * 24 * 365, samesite="lax")
        return response

    @app.post("/profiles/{profile_id}/rename")
    def rename_profile(profile_id: int, name: str = Form(...)):
        name = name.strip()
        if not name:
            return RedirectResponse("/profiles", status_code=303)
        with session_factory() as session:
            profile = session.get(Profile, profile_id)
            if profile is None:
                return JSONResponse({"error": "Profile not found."}, status_code=404)
            profile.name = name
            session.commit()
        return RedirectResponse("/profiles", status_code=303)

    def _onboarding_status(session, profile, settings: Settings) -> dict:
        """Whether the active profile has what it needs for a search to be
        useful. Computed fresh from existing data on every render -- no
        persisted "setup complete" flag to drift from reality. Local LLM
        setup is deliberately not part of `all_done`: search still works
        (rule-based keyword fallback) without it, it's just better with
        one, so it's surfaced as an optional suggestion, not a checklist
        item that blocks the banner from going away.

        `visible` additionally folds in the global tutorials_enabled switch
        and this profile's own "don't show this again" dismissal -- `
        all_done` alone used to be the only thing hiding the banner."""
        resume_done = bool(profile.resume_text)
        preferences_done = bool(effective_preferences(profile, settings).target_titles)
        all_done = resume_done and preferences_done
        return {
            "resume_done": resume_done,
            "preferences_done": preferences_done,
            "all_done": all_done,
            "visible": not all_done and tutorials.is_tutorial_visible(
                session, settings, profile.id, "onboarding_checklist"
            ),
        }

    RECENT_POSTING_WINDOW_DAYS = 7

    def _job_list_context(
        session, profile, status: str | None, sort: str | None = None, recent_only: bool = False
    ) -> dict:
        """Everything the jobs list + stats bar + filter pills need --
        shared between the full index page and the /jobs/panel partial the
        page polls while a search is running, so the two can never drift
        out of sync with each other."""
        cutoff = utc_now() - dt.timedelta(days=RECENT_POSTING_WINDOW_DAYS)
        query = session.query(JobPosting).filter(JobPosting.profile_id == profile.id)
        if status:
            query = query.filter(JobPosting.status == ApplicationStatus(status))
        if recent_only:
            query = query.filter(JobPosting.posted_at.isnot(None), JobPosting.posted_at >= cutoff)
        if sort == "recent":
            # Nulls-last without relying on SQLite's NULLS LAST support
            # (only in 3.30+): sort by "is this null" first (False/0 before
            # True/1), then by the real date within each group.
            query = query.order_by(JobPosting.posted_at.is_(None), JobPosting.posted_at.desc())
        else:
            sort = "fit"
            query = query.order_by(JobPosting.fit_score.desc())
        jobs = query.all()

        # "Considered" = every posting fetched and scored (matched or
        # not), regardless of the current status filter above — this is
        # a standing total, not affected by which status tab is open.
        considered_count = (
            session.query(SeenPosting).filter(SeenPosting.profile_id == profile.id).count()
        )
        matched_count = (
            session.query(JobPosting).filter(JobPosting.profile_id == profile.id).count()
        )
        recent_count = (
            session.query(JobPosting)
            .filter(
                JobPosting.profile_id == profile.id,
                JobPosting.posted_at.isnot(None),
                JobPosting.posted_at >= cutoff,
            )
            .count()
        )

        status_counts_rows = (
            session.query(JobPosting.status, func.count(JobPosting.id))
            .filter(JobPosting.profile_id == profile.id)
            .group_by(JobPosting.status)
            .all()
        )
        status_counts = {s.value: 0 for s in ApplicationStatus}
        for status_value, count in status_counts_rows:
            status_counts[status_value.value] = count
        gap_by_job = {
            item["job"]["id"]: item
            for item in saved_job_gaps(session, profile)
        }
        projects = (
            session.query(Project)
            .filter(Project.profile_id == profile.id)
            .order_by(Project.id.desc())
            .limit(10)
            .all()
        )
        score_impact_by_job = {}
        for impact in resume_page_status(profile, session)["score_impacts"]:
            # Resume status returns newest snapshots first; keep that
            # explanation when a job has been rescored more than once.
            score_impact_by_job.setdefault(impact["job_id"], impact)

        def job_filter_href(new_status=Ellipsis, new_sort=Ellipsis, new_recent=Ellipsis) -> str:
            """Builds a /?... link for a filter/sort pill, changing only
            the one dimension it's given and preserving the other two --
            so switching status doesn't silently drop an active "posted
            this week" filter or sort choice, and vice versa."""
            eff_status = status if new_status is Ellipsis else new_status
            eff_sort = sort if new_sort is Ellipsis else new_sort
            eff_recent = recent_only if new_recent is Ellipsis else new_recent
            params = {}
            if eff_status:
                params["status"] = eff_status
            if eff_sort and eff_sort != "fit":
                params["sort"] = eff_sort
            if eff_recent:
                params["recent"] = "1"
            query = urlencode(params)
            return "/" + (f"?{query}" if query else "")

        model_readiness = _model_readiness()

        return {
            "jobs": jobs,
            "statuses": [s.value for s in ApplicationStatus],
            "current_filter": status or "",
            "current_sort": sort,
            "current_recent": recent_only,
            "recent_window_days": RECENT_POSTING_WINDOW_DAYS,
            "considered_count": considered_count,
            "matched_count": matched_count,
            "recent_count": recent_count,
            "status_counts": status_counts,
            "gap_by_job": gap_by_job,
            "projects": [project_status(project) for project in projects],
            "score_impact_by_job": score_impact_by_job,
            "onboarding": _onboarding_status(session, profile, settings),
            "model_ready": model_readiness["ready"],
            "model_not_ready_reason": model_readiness["reason"],
            "job_filter_href": job_filter_href,
        }

    def _model_readiness() -> dict:
        """Only Ollama has a locally-pulled-model concept -- Anthropic (or
        no provider) has nothing to download, so is always "ready" here.
        Cheap: same /api/tags call _provider_diagnostics() already makes
        for Settings, not a full generation.

        "reason" distinguishes three states that need different prompts:
        Ollama isn't installed at all (most actionable -- the Jobs page
        should point straight at the install flow, not a vague "not
        ready"), it's installed but not currently running, or it's
        running but the configured model hasn't been pulled yet."""
        if settings.llm.provider != "ollama":
            return {"ready": True, "reason": None}
        diagnostics = _provider_diagnostics()
        if diagnostics.configured_model_available:
            return {"ready": True, "reason": None}
        if diagnostics.executable_path is None:
            return {"ready": False, "reason": "not_installed"}
        if not diagnostics.service_reachable:
            return {"ready": False, "reason": "not_running"}
        return {"ready": False, "reason": "model_missing"}

    @app.get("/jobs/panel")
    def jobs_panel(request: Request, status: str | None = None, sort: str | None = None, recent: str | None = None):
        """Polled by the dashboard while a search is running so newly
        matched jobs (and the stats bar / filter counts) appear as they're
        scored, instead of only after a full page reload once the search
        finishes."""
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            context = _job_list_context(session, profile, status, sort, recent == "1")
        return JSONResponse({
            "stats_html": templates.env.get_template("_stats_bar.html").render(context),
            "jobs_html": templates.env.get_template("_jobs_panel.html").render(context),
        })

    @app.get("/")
    def index(request: Request, status: str | None = None, sort: str | None = None, recent: str | None = None):
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            context = _job_list_context(session, profile, status, sort, recent == "1")
            reminders = get_due_reminders(session, profile)

            return templates.TemplateResponse(
                request=request,
                name="index.html",
                context={
                    **context,
                    "reminders": reminders,
                    "search_running": state["search_running"],
                    "last_search_result": state["last_search_result"],
                    "run_id": state["run_id"],
                    "active_profile": {"id": profile.id, "name": profile.name},
                    **_scheduler_status(profile.id),
                },
            )

    @app.post("/jobs/{job_id}/status")
    def update_status(request: Request, job_id: int, new_status: str = Form(...)):
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            job = session.get(JobPosting, job_id)
            if job and job.profile_id == profile.id:
                try:
                    job.status = ApplicationStatus(new_status)
                except ValueError:
                    return JSONResponse({"error": "Invalid application status."}, status_code=400)
                session.commit()
        return RedirectResponse("/", status_code=303)

    @app.post("/jobs/manual")
    def add_manual_job(
        request: Request,
        company: str = Form(...),
        title: str = Form(...),
        url: str = Form(...),
        location: str = Form(""),
        remote: str = Form(""),
        salary_min: str = Form(""),
        salary_max: str = Form(""),
        description: str = Form(""),
    ):
        """A posting Hanarr's own connectors didn't find (e.g. a referral,
        a source not yet supported) can still be tracked and scored here --
        same fit-scoring path a real search cycle uses (market_analysis_llm,
        with matching.py's own keyword fallback when no LLM is configured),
        just for one job entered by hand instead of fetched."""
        company = company.strip()
        title = title.strip()
        url = url.strip()
        if not company or not title or not url:
            return JSONResponse({"error": "Company, title, and URL are required."}, status_code=400)

        def _parse_salary(raw: str) -> float | None:
            raw = raw.strip()
            if not raw:
                return None
            try:
                return float(raw)
            except ValueError:
                return None

        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            resume_summary = json.loads(profile.resume_summary_json or "{}")
            raw_job = RawJobPosting(
                source="manual",
                external_id=f"manual-{uuid.uuid4().hex}",
                company=company,
                title=title,
                location=location.strip(),
                remote=remote == "on",
                url=url,
                description=description.strip(),
                salary_min=_parse_salary(salary_min),
                salary_max=_parse_salary(salary_max),
            )
            try:
                score, rationale, method = score_fit(
                    raw_job, resume_summary, profile.resume_text or "",
                    effective_preferences(profile, settings), market_analysis_llm
                )
            except LLMScoringFailedError as exc:
                return JSONResponse({"error": f"Could not score this job — {exc}"[:300]}, status_code=502)
            job = JobPosting(
                profile_id=profile.id,
                source=raw_job.source,
                external_id=raw_job.external_id,
                company=raw_job.company,
                title=raw_job.title,
                location=raw_job.location,
                remote=raw_job.remote,
                url=raw_job.url,
                description=raw_job.description,
                salary_min=raw_job.salary_min,
                salary_max=raw_job.salary_max,
                fit_score=score,
                fit_rationale=rationale,
                fit_score_method=method,
            )
            session.add(job)
            session.commit()
        return RedirectResponse("/", status_code=303)

    @app.post("/api/jobs/{job_id}/skill-gaps/analyze")
    def analyze_job_skill_gaps(request: Request, job_id: int):
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            job = session.get(JobPosting, job_id)
            if job is None or job.profile_id != profile.id:
                return JSONResponse({"error": "Saved job not found."}, status_code=404)
            try:
                result = analyze_job(session, profile, job, market_analysis_llm)
                session.commit()
            except Exception as exc:  # noqa: BLE001
                # Without this, any failure here (a DB hiccup, an LLM/network
                # error not already absorbed by analyze_job's own fallback,
                # etc.) surfaced as a bare 500 with no detail -- "Improve my
                # fit" would just silently revert with zero indication of
                # what actually went wrong, reported as "the button doesn't
                # work" with nothing to debug from.
                logger.exception("Skill-gap analysis failed for job %d", job_id)
                message = f"Analysis failed — {exc}"
                return JSONResponse({"error": message[:300]}, status_code=500)
            return JSONResponse(result)

    @app.post("/api/jobs/{job_id}/rescore")
    def rescore_single_job(request: Request, job_id: int):
        """Re-scores one saved job against the current resume/preferences/LLM
        -- the single-job counterpart to "Rescore all jobs". Synchronous
        (one LLM call, same as the other per-job action buttons) rather than
        the background-thread-plus-polling shape the bulk version needs."""
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            job = session.get(JobPosting, job_id)
            if job is None or job.profile_id != profile.id:
                return JSONResponse({"error": "Saved job not found."}, status_code=404)

            raw = RawJobPosting(
                source=job.source, external_id=job.external_id, company=job.company, title=job.title,
                location=job.location, remote=job.remote, url=job.url, description=job.description,
                salary_min=job.salary_min, salary_max=job.salary_max, posted_at=job.posted_at,
            )
            try:
                score, rationale, method = score_fit(
                    raw, json.loads(profile.resume_summary_json or "{}"), profile.resume_text or "",
                    effective_preferences(profile, settings), market_analysis_llm,
                )
            except LLMScoringFailedError as exc:
                return JSONResponse({"error": f"Could not rescore this job — {exc}"[:300]}, status_code=502)

            before = job.fit_score
            job.fit_score, job.fit_rationale, job.fit_score_method = score, rationale, method
            session.add(ScoreSnapshot(
                profile_id=profile.id, job_id=job.id, fit_score=score, fit_rationale=rationale,
                trigger="rescore_single",
                scorer_metadata_json=json.dumps({"before_score": before, "after_score": score, "method": method}),
            ))
            session.commit()
            return JSONResponse({"fit_score": score, "fit_rationale": rationale, "fit_score_method": method})

    @app.post("/api/jobs/{job_id}/cover-letter")
    def draft_job_cover_letter(request: Request, job_id: int):
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            job = session.get(JobPosting, job_id)
            if job is None or job.profile_id != profile.id:
                return JSONResponse({"error": "Saved job not found."}, status_code=404)
            try:
                generate_and_store_cover_letter(profile, job, resume_writer_llm)
                session.commit()
            except Exception as exc:  # noqa: BLE001
                logger.exception("Cover letter drafting failed for job %d", job_id)
                return JSONResponse({"error": f"Drafting failed — {exc}"[:300]}, status_code=500)
            return JSONResponse({
                "cover_letter": job.cover_letter,
                "source": job.cover_letter_source,
                "generated_at": job.cover_letter_generated_at.isoformat() if job.cover_letter_generated_at else None,
            })

    @app.post("/api/jobs/{job_id}/outreach")
    async def draft_job_outreach_email(request: Request, job_id: int):
        """Drafts a networking email to a contact the user already found
        themselves -- Hanarr never looks up who to contact, see
        outreach.py's module docstring for why."""
        payload = await request.json()
        if not isinstance(payload, dict):
            return JSONResponse({"error": "contact must be given as an object."}, status_code=400)
        contact = str(payload.get("contact", "")).strip()
        if not contact:
            return JSONResponse({"error": "A contact (name, email, or LinkedIn URL) is required."}, status_code=400)
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            job = session.get(JobPosting, job_id)
            if job is None or job.profile_id != profile.id:
                return JSONResponse({"error": "Saved job not found."}, status_code=404)
            try:
                generate_and_store_outreach_email(profile, job, contact, resume_writer_llm)
                session.commit()
            except Exception as exc:  # noqa: BLE001
                logger.exception("Outreach email drafting failed for job %d", job_id)
                return JSONResponse({"error": f"Drafting failed — {exc}"[:300]}, status_code=500)
            return JSONResponse({
                "contact": job.outreach_contact,
                "outreach_email": job.outreach_email,
                "source": job.outreach_email_source,
                "generated_at": job.outreach_email_generated_at.isoformat() if job.outreach_email_generated_at else None,
            })

    @app.get("/api/jobs/{job_id}/skill-gaps")
    def get_job_skill_gaps(request: Request, job_id: int):
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            job = session.get(JobPosting, job_id)
            if job is None or job.profile_id != profile.id:
                return JSONResponse({"error": "Saved job not found."}, status_code=404)
            result = saved_job_gap(session, profile, job_id)
            if result is None:
                return JSONResponse(
                    {"job": {"id": job.id, "title": job.title, "company": job.company},
                     "analyzed": False, "gaps": [], "gap_counts": {}}
                )
            return JSONResponse({"analyzed": True, **result})

    @app.patch("/api/skills/{skill_id}/profile")
    async def update_profile_skill(skill_id: int, request: Request):
        payload = await request.json()
        if not isinstance(payload, dict):
            return JSONResponse({"error": "skill update must be an object."}, status_code=400)
        try:
            proficiency = payload.get("proficiency")
            confidence = payload.get("confidence")
            proficiency = None if proficiency in (None, "") else float(proficiency)
            confidence = None if confidence in (None, "") else float(confidence)
        except (TypeError, ValueError):
            return JSONResponse({"error": "proficiency and confidence must be numbers from 0 to 1."}, status_code=400)
        if any(value is not None and not 0 <= value <= 1 for value in (proficiency, confidence)):
            return JSONResponse({"error": "proficiency and confidence must be numbers from 0 to 1."}, status_code=400)
        evidence = str(payload.get("evidence", "")).strip()
        if not evidence:
            return JSONResponse({"error": "evidence is required for a capability override."}, status_code=400)
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            skill = session.get(Skill, skill_id)
            if skill is None:
                return JSONResponse({"error": "Skill not found."}, status_code=404)
            row = session.query(ProfileSkill).filter_by(
                profile_id=profile.id, skill_id=skill.id
            ).one_or_none()
            if row is None:
                row = ProfileSkill(profile_id=profile.id, skill_id=skill.id)
                session.add(row)
            row.proficiency = proficiency
            row.confidence = confidence
            row.evidence = evidence
            row.source = "manual"
            session.commit()
            return JSONResponse({"skill": skill.name, "capability": {
                "proficiency": row.proficiency, "confidence": row.confidence,
                "evidence": row.evidence, "source": row.source,
            }, "proven": False})

    @app.post("/api/skills/{skill_id}/interview/start")
    def start_skill_interview(request: Request, skill_id: int):
        """Generates 2-4 short, bounded questions testing whether a claimed
        skill still holds up -- never an open-ended chat, same explainable,
        bounded shape as every other LLM-backed step in the app."""
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            try:
                interview = start_interview(session, profile.id, skill_id, evaluator_llm)
                session.commit()
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            return JSONResponse(interview_status(interview), status_code=201)

    @app.post("/api/skills/{skill_id}/interview/{interview_id}/submit")
    async def submit_skill_interview(request: Request, skill_id: int, interview_id: int):
        payload = await request.json()
        answers = payload.get("answers") if isinstance(payload, dict) else None
        if not isinstance(answers, list) or not all(isinstance(item, str) for item in answers):
            return JSONResponse({"error": "answers must be a list of strings."}, status_code=400)
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            interview_check = session.get(SkillInterview, interview_id)
            if interview_check is None or interview_check.skill_id != skill_id:
                return JSONResponse({"error": "Interview not found."}, status_code=404)
            try:
                interview = submit_interview(session, profile.id, interview_id, answers, evaluator_llm)
                session.commit()
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            return JSONResponse(interview_status(interview))

    @app.post("/api/star/questions/generate")
    async def generate_star_questions_route(request: Request):
        payload = {}
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        job_id = payload.get("job_id") if isinstance(payload, dict) else None
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            try:
                questions = generate_star_questions(
                    session, profile, curriculum_llm, job_id=int(job_id) if job_id else None
                )
                session.commit()
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            return JSONResponse(
                {"questions": [question_dict(q, None) for q in questions]}, status_code=201
            )

    @app.post("/api/star/questions/{question_id}/story")
    def start_star_story(request: Request, question_id: int):
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            try:
                story = get_or_create_story(session, profile.id, question_id)
                session.commit()
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=404)
            return JSONResponse(question_dict(story.question, story))

    @app.patch("/api/star/stories/{story_id}")
    async def save_star_story(request: Request, story_id: int):
        payload = await request.json()
        if not isinstance(payload, dict):
            return JSONResponse({"error": "story update must be an object."}, status_code=400)
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            try:
                story = save_story_draft(
                    session, profile.id, story_id,
                    str(payload.get("situation", "")), str(payload.get("task", "")),
                    str(payload.get("action", "")), str(payload.get("result", "")),
                )
                session.commit()
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            return JSONResponse(question_dict(story.question, story))

    @app.post("/api/star/stories/{story_id}/review")
    def review_star_story(request: Request, story_id: int):
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            try:
                story = review_story(session, profile.id, story_id, evaluator_llm)
                session.commit()
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            return JSONResponse(question_dict(story.question, story))

    @app.post("/api/star/stories/{story_id}/status")
    async def set_star_story_status(request: Request, story_id: int):
        payload = await request.json()
        status = str(payload.get("status", "")) if isinstance(payload, dict) else ""
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            try:
                story = set_story_status(session, profile.id, story_id, status)
                session.commit()
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            return JSONResponse(question_dict(story.question, story))

    @app.get("/prep")
    def prep_page(request: Request, job_id: int | None = None):
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            target_job = None
            if job_id is not None:
                job = session.get(JobPosting, job_id)
                if job is not None and job.profile_id == profile.id:
                    target_job = {"id": job.id, "title": job.title, "company": job.company}
            return templates.TemplateResponse(
                request=request,
                name="prep.html",
                context={
                    **prep_page_status(session, profile),
                    "active_profile": {"id": profile.id, "name": profile.name},
                    "target_job": target_job,
                },
            )

    def _chat_message_dict(m: ChatMessage) -> dict:
        return {
            "id": m.id,
            "role": m.role.value,
            "content": m.content,
            "action": json.loads(m.action_json) if m.action_json else None,
            "action_status": m.action_status,
        }

    @app.get("/coach")
    def coach_page(request: Request):
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            messages = recent_messages(session, profile.id)
            readiness = _model_readiness()
            llm_ready = settings.llm.provider != "none" and readiness["ready"]
            return templates.TemplateResponse(
                request=request,
                name="coach.html",
                context={
                    "messages": [_chat_message_dict(m) for m in messages],
                    "llm_ready": llm_ready,
                    "llm_provider": settings.llm.provider,
                    "model_not_ready_reason": readiness["reason"],
                    "active_profile": {"id": profile.id, "name": profile.name},
                },
            )

    @app.post("/api/coach/messages")
    async def post_coach_message(request: Request):
        """There's no deterministic-fallback equivalent for open-ended
        chat, so this needs a real LLM -- `provider: none` is refused up
        front rather than attempted and failed. A returned action is only
        ever a proposal (type="action") -- see the confirm route below for
        the one place it can actually run."""
        payload = await request.json()
        if not isinstance(payload, dict):
            return JSONResponse({"error": "message must be an object."}, status_code=400)
        question = str(payload.get("message", "")).strip()
        if not question:
            return JSONResponse({"error": "message is required."}, status_code=400)
        if settings.llm.provider == "none":
            return JSONResponse(
                {"error": "Coach needs an LLM provider configured in Settings → App config."},
                status_code=400,
            )
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            try:
                result = ask_coach(session, profile, coach_llm, question)
            except CoachError as exc:
                session.commit()  # keep the persisted user message even though the answer failed
                return JSONResponse({"error": str(exc)}, status_code=502)
            session.commit()
            return JSONResponse(result)

    @app.post("/api/coach/messages/{message_id}/confirm")
    def confirm_coach_action(request: Request, message_id: int):
        """The only place a Coach-proposed action actually executes --
        reachable only by an explicit button click, never from the LLM's
        own output. Reuses the exact same backend functions the rest of
        the dashboard's buttons call (coach_actions.py)."""
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            try:
                result = confirm_action(session, profile, coach_llm, message_id)
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            session.commit()
            return JSONResponse({"result": result})

    @app.post("/api/coach/messages/{message_id}/decline")
    def decline_coach_action(request: Request, message_id: int):
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            try:
                decline_action(session, profile, message_id)
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            session.commit()
            return JSONResponse({"status": "declined"})

    @app.post("/api/coaching-projects")
    async def create_project(request: Request):
        payload = await request.json()
        try:
            mode = ProjectMode(str(payload.get("mode", "")))
            job_id = int(payload["job_id"]) if payload.get("job_id") is not None else None
            skill_id = int(payload["skill_id"]) if payload.get("skill_id") is not None else None
        except (ValueError, TypeError, KeyError):
            return JSONResponse({"error": "mode, job_id, and skill_id must be valid values."}, status_code=400)
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            try:
                result = create_coaching_project(
                    session, profile.id, mode, curriculum_llm, job_id=job_id, skill_id=skill_id
                )
                session.commit()
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            return JSONResponse(result, status_code=201)

    @app.get("/api/coaching-projects")
    def list_projects(request: Request):
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            projects = (
                session.query(Project)
                .filter(Project.profile_id == profile.id)
                .order_by(Project.id.desc())
                .all()
            )
            return JSONResponse({"projects": [project_status(project) for project in projects]})

    @app.get("/api/coaching-projects/{project_id}")
    def get_project(request: Request, project_id: int):
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            project = session.get(Project, project_id)
            if project is None or project.profile_id != profile.id:
                return JSONResponse({"error": "Coaching project not found."}, status_code=404)
            return JSONResponse(project_status(project))

    @app.post("/api/coaching-projects/{project_id}/tasks/{task_id}/status")
    async def update_project_task_status(project_id: int, task_id: int, request: Request):
        payload = await request.json()
        try:
            task_status = ProjectTaskStatus(str(payload.get("status", "")))
        except (ValueError, TypeError):
            return JSONResponse({"error": "status must be todo, in_progress, or done."}, status_code=400)
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            project = session.get(Project, project_id)
            task = session.get(ProjectTask, task_id)
            if project is None or project.profile_id != profile.id or task is None or task.project_id != project.id:
                return JSONResponse({"error": "Coaching task not found."}, status_code=404)
            task.status = task_status
            if task_status is ProjectTaskStatus.IN_PROGRESS and project.status is ProjectStatus.PLANNED:
                project.status = ProjectStatus.ACTIVE
            session.commit()
            return JSONResponse(project_status(project))

    @app.post("/api/coaching-projects/{project_id}/cancel")
    def cancel_project(request: Request, project_id: int):
        """Lets a project be abandoned honestly instead of sitting "active"
        forever with no way to close it out -- the counterpart to the
        automatic completion that already happens on a passed evaluation."""
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            project = session.get(Project, project_id)
            if project is None or project.profile_id != profile.id:
                return JSONResponse({"error": "Coaching project not found."}, status_code=404)
            if project.status in (ProjectStatus.COMPLETED, ProjectStatus.CANCELLED):
                return JSONResponse({"error": f"Project is already {project.status.value}."}, status_code=400)
            project.status = ProjectStatus.CANCELLED
            session.commit()
            return JSONResponse(project_status(project))

    @app.post("/api/coaching-projects/{project_id}/complete")
    def complete_project(request: Request, project_id: int):
        """A manual counterpart to the automatic completion a passed
        evaluation already triggers -- for work you're satisfied with
        without ever submitting it for an LLM review. Unlike a passed
        evaluation, this never touches ProvenSkill/ProfileSkill confidence
        or JobSkill.gap_status: those are evidence-backed claims an
        unreviewed self-declaration shouldn't silently grant."""
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            project = session.get(Project, project_id)
            if project is None or project.profile_id != profile.id:
                return JSONResponse({"error": "Coaching project not found."}, status_code=404)
            if project.status in (ProjectStatus.COMPLETED, ProjectStatus.CANCELLED):
                return JSONResponse({"error": f"Project is already {project.status.value}."}, status_code=400)
            project.status = ProjectStatus.COMPLETED
            project.completed_at = utc_now()
            session.commit()
            return JSONResponse(project_status(project))

    @app.get("/api/tutorials/{tutorial_key}/status")
    def tutorial_status_route(request: Request, tutorial_key: str):
        """Polled by self-contained tutorial-popup partials (see
        _guide_pointer_banner.html) the same way the update banner polls
        /update/status -- lets a popup decide its own visibility from
        JS without every page route needing to compute and pass it down,
        which is how the onboarding checklist ended up only ever wired into
        two of ten pages."""
        if tutorial_key not in tutorials.TUTORIAL_KEYS:
            return JSONResponse({"error": f"Unknown tutorial_key {tutorial_key!r}."}, status_code=404)
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            visible = tutorials.is_tutorial_visible(session, settings, profile.id, tutorial_key)
        return JSONResponse({"visible": visible})

    @app.post("/api/tutorials/dismiss")
    async def dismiss_tutorial_route(request: Request):
        form = await request.form()
        tutorial_key = str(form.get("tutorial_key", "")).strip()
        if tutorial_key not in tutorials.TUTORIAL_KEYS:
            return JSONResponse({"error": f"Unknown tutorial_key {tutorial_key!r}."}, status_code=400)
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            tutorials.dismiss_tutorial(session, profile.id, tutorial_key)
            session.commit()
        return JSONResponse({"status": "dismissed", "tutorial_key": tutorial_key})

    @app.post("/api/tutorials/reset")
    def reset_tutorials_route(request: Request):
        """Settings' "Reset dismissed tutorials" -- brings back every
        tutorial this profile previously dismissed, independent of the
        global tutorials_enabled switch (that's a separate on/off, not
        reset by this)."""
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            tutorials.reset_dismissed_tutorials(session, profile.id)
            session.commit()
        return JSONResponse({"status": "reset"})

    @app.post("/api/coaching-projects/{project_id}/submissions")
    async def create_written_project_submission(project_id: int, request: Request):
        payload = await request.json()
        if not isinstance(payload, dict):
            return JSONResponse({"error": "submission payload must be an object."}, status_code=400)
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            try:
                submission = create_written_submission(
                    session, profile.id, project_id, str(payload.get("content", "")), str(payload.get("title", ""))
                )
                session.commit()
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            return JSONResponse(submission_status(submission), status_code=201)

    @app.post("/api/coaching-projects/{project_id}/submissions/files")
    async def create_local_project_submission(
        request: Request,
        project_id: int,
        files: list[UploadFile] = File(...),
        title: str = Form(""),
    ):
        if not files or len(files) > MAX_FILES:
            return JSONResponse({"error": f"local submission must contain between 1 and {MAX_FILES} files."}, status_code=413)
        uploaded = [(file.filename or "", file.file) for file in files]
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            try:
                submission = create_local_submission(
                    session, profile.id, project_id, uploaded, Path(settings.data_dir) / "submissions", title
                )
                session.commit()
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=413 if "exceed" in str(exc) or "bytes" in str(exc) else 400)
            return JSONResponse(submission_status(submission), status_code=201)

    @app.post("/api/coaching-projects/{project_id}/submissions/github")
    async def create_github_project_submission(project_id: int, request: Request):
        payload = await request.json()
        if not isinstance(payload, dict):
            return JSONResponse({"error": "submission payload must be an object."}, status_code=400)
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            try:
                submission = create_github_submission(
                    session,
                    profile.id,
                    project_id,
                    str(payload.get("reference", "")),
                    str(payload.get("ref", "")),
                    str(payload.get("title", "")),
                )
                session.commit()
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            return JSONResponse(submission_status(submission), status_code=201)

    @app.post("/api/coaching-projects/{project_id}/submissions/{submission_id}/github/fetch")
    def fetch_github_project_submission(request: Request, project_id: int, submission_id: int):
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            submission = session.get(ProjectSubmission, submission_id)
            if submission is None or submission.project_id != project_id:
                return JSONResponse({"error": "Submission not found."}, status_code=404)
            try:
                submission = fetch_github_submission(session, profile.id, submission_id)
                session.commit()
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            return JSONResponse(submission_status(submission))

    @app.get("/api/coaching-projects/{project_id}/submissions")
    def list_project_submissions(request: Request, project_id: int):
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            project = session.get(Project, project_id)
            if project is None or project.profile_id != profile.id:
                return JSONResponse({"error": "Coaching project not found."}, status_code=404)
            return JSONResponse({"submissions": [submission_status(item) for item in project.submissions]})

    @app.post("/api/coaching-projects/{project_id}/submissions/{submission_id}/submit")
    def submit_project_submission(request: Request, project_id: int, submission_id: int):
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            submission = session.get(ProjectSubmission, submission_id)
            if submission is None or submission.project_id != project_id:
                return JSONResponse({"error": "Submission not found."}, status_code=404)
            try:
                submission = submit_submission(session, profile.id, submission_id)
                session.commit()
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            return JSONResponse(submission_status(submission))

    @app.post("/api/coaching-projects/{project_id}/submissions/{submission_id}/evaluate")
    def evaluate_project_submission(request: Request, project_id: int, submission_id: int):
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            submission = session.get(ProjectSubmission, submission_id)
            if submission is None or submission.project_id != project_id:
                return JSONResponse({"error": "Submission not found."}, status_code=404)
            try:
                evaluation = evaluate_submission(session, profile.id, submission_id, evaluator_llm)
                proposal = None
                if evaluation.passed:
                    project = submission.project
                    if project.status is not ProjectStatus.COMPLETED:
                        project.status = ProjectStatus.COMPLETED
                        project.completed_at = utc_now()
                    new_confidence = evaluation.score / 100 if evaluation.score is not None else None
                    skill_ids = set()
                    for project_skill in project.skills:
                        skill_ids.add(project_skill.skill_id)
                        if session.query(ProvenSkill).filter_by(
                            profile_id=profile.id, skill_id=project_skill.skill_id
                        ).first() is None:
                            session.add(ProvenSkill(
                                profile_id=profile.id, skill_id=project_skill.skill_id,
                                project_id=project.id, evaluation_id=evaluation.id,
                                evidence=evaluation.feedback or project.target_outcome,
                            ))
                        if new_confidence is None:
                            continue
                        profile_skill = session.query(ProfileSkill).filter_by(
                            profile_id=profile.id, skill_id=project_skill.skill_id
                        ).first()
                        note = f'Coaching project "{project.title}" passed evaluation ({evaluation.score:.0f}/100).'
                        if profile_skill is None:
                            session.add(ProfileSkill(
                                profile_id=profile.id, skill_id=project_skill.skill_id,
                                source="project", confidence=new_confidence, evidence=note,
                            ))
                        elif profile_skill.source == "project":
                            profile_skill.confidence = max(profile_skill.confidence or 0.0, new_confidence)
                            profile_skill.evidence = note
                        elif new_confidence > (profile_skill.confidence or 0.0):
                            # Never silently overwrite a higher-trust resume/manual
                            # source's confidence without saying so -- raise it (never
                            # lower it) and record the corroboration in evidence text.
                            profile_skill.confidence = new_confidence
                            profile_skill.evidence = f"{profile_skill.evidence} Corroborated by a passed coaching project: {note}".strip()
                    affected_job_ids = {link.job_id for link in project.affected_jobs}
                    if skill_ids and affected_job_ids:
                        for job_skill in session.query(JobSkill).filter(
                            JobSkill.job_id.in_(affected_job_ids), JobSkill.skill_id.in_(skill_ids)
                        ):
                            job_skill.gap_status = SkillGapStatus.SATISFIED
                            if new_confidence is not None:
                                job_skill.confidence = max(job_skill.confidence or 0.0, new_confidence)
                            job_skill.rationale = f'Satisfied by a passed coaching project: "{project.title}".'
                            job_skill.analyzed_at = utc_now()
                    proposal = session.query(ResumeProposal).filter(
                        ResumeProposal.profile_id == profile.id,
                        ResumeProposal.project_id == project.id,
                        ResumeProposal.status == "pending",
                    ).order_by(ResumeProposal.id.desc()).first()
                    if proposal is None:
                        proposal = create_resume_proposal(session, profile.id, project, evaluation, resume_writer_llm)
                session.commit()
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            result = submission_status(submission)
            if proposal is not None:
                result["resume_proposal_id"] = proposal.id
            return JSONResponse(result)

    @app.post("/api/coaching-projects/{project_id}/submissions/{submission_id}/resubmit")
    def resubmit_project_submission(request: Request, project_id: int, submission_id: int):
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            submission = session.get(ProjectSubmission, submission_id)
            if submission is None or submission.project_id != project_id:
                return JSONResponse({"error": "Submission not found."}, status_code=404)
            try:
                resubmit_submission(session, profile.id, submission_id)
                session.commit()
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            return JSONResponse(submission_status(submission))

    @app.get("/coaching")
    def coaching(request: Request):
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            projects = (
                session.query(Project)
                .filter(Project.profile_id == profile.id)
                .order_by(Project.id.desc())
                .all()
            )
            suggestions = coaching_suggestions(session, profile)
            demand = market_demand_summary(session, profile)
            now = utc_now()
            project_cards = []
            for project in projects:
                status = project_status(project)
                status["affected_jobs"] = [
                    {"id": link.job.id, "title": link.job.title, "company": link.job.company,
                     "url": link.job.url, "dashboard_url": f"/#job-{link.job.id}"}
                    for link in project.affected_jobs
                ]
                status["stale"] = _project_is_stale(project, now)
                project_cards.append(status)
            return templates.TemplateResponse(
                request=request,
                name="coaching.html",
                context={
                    "projects": project_cards, "suggestions": suggestions, "market_demand": demand,
                    "active_profile": {"id": profile.id, "name": profile.name},
                },
            )

    @app.post("/api/resume/proposals/{proposal_id}/approve")
    def approve_proposal(request: Request, proposal_id: int):
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            try:
                result = approve_resume_proposal(session, settings, profile.id, proposal_id, resume_writer_llm)
                session.commit()
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            except LLMScoringFailedError as exc:
                return JSONResponse({"error": f"Resume approved, but rescoring affected jobs failed — {exc}"[:300]}, status_code=502)
            return JSONResponse(result)

    @app.post("/api/resume/proposals/{proposal_id}/reject")
    def reject_proposal(request: Request, proposal_id: int):
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            try:
                proposal = reject_resume_proposal(session, profile.id, proposal_id)
                session.commit()
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            return JSONResponse({"id": proposal.id, "status": proposal.status.value})

    @app.post("/api/resume/versions/{version_id}/rollback")
    def rollback_resume(request: Request, version_id: int):
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            version = session.get(ResumeVersion, version_id)
            if version is None or version.profile_id != profile.id:
                return JSONResponse({"error": "Resume version not found."}, status_code=404)
            pending = ResumeProposal(
                profile_id=profile.id,
                base_version_id=next((v.id for v in profile.resume_versions if v.is_active), None),
                proposed_content=version.content,
                diff="Rollback proposal to version %s." % version.id,
                rationale="Explicit rollback; approve to activate this prior version.",
            )
            session.add(pending)
            session.commit()
            return JSONResponse({"id": pending.id, "status": pending.status.value})

    @app.post("/api/resume/versions/{version_id}/label")
    async def label_resume_version(version_id: int, request: Request):
        payload = await request.json()
        label = str(payload.get("label", "")).strip()[:100] if isinstance(payload, dict) else ""
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            version = session.get(ResumeVersion, version_id)
            if version is None or version.profile_id != profile.id:
                return JSONResponse({"error": "Resume version not found."}, status_code=404)
            version.label = label or None
            session.commit()
            return JSONResponse({"id": version.id, "label": version.label})

    @app.get("/resume/download")
    def download_resume(request: Request):
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            active_version = next(
                (v for v in reversed(profile.resume_versions) if v.is_active), None
            )
            # Only the extracted plain text is retained -- the original
            # uploaded PDF/DOCX bytes aren't kept -- so this is always a
            # .txt download, never a reproduction of the original file.
            content = active_version.content if active_version else profile.resume_text
            if not content:
                return JSONResponse({"error": "No resume on file yet."}, status_code=404)
            base_name = Path(profile.resume_original_filename or "resume").stem or "resume"
            return PlainTextResponse(
                content,
                headers={"Content-Disposition": f'attachment; filename="{base_name}.txt"'},
            )

    @app.get("/resume")
    def resume_page(request: Request):
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            return templates.TemplateResponse(
                request=request, name="resume.html",
                context={
                    "resume": resume_page_status(profile, session),
                    "active_profile": {"id": profile.id, "name": profile.name},
                }
            )

    @app.get("/guide")
    def guide_page(request: Request):
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            return templates.TemplateResponse(
                request=request, name="guide.html",
                context={"active_profile": {"id": profile.id, "name": profile.name}},
            )

    @app.get("/skills")
    def skills_page(request: Request):
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            skills = profile_skill_page(session, profile)
            for skill in skills:
                skill["interviews"] = interviews_for_skill(session, profile.id, skill["id"])
            return templates.TemplateResponse(
                request=request,
                name="skills.html",
                context={
                    "skills": skills,
                    "active_profile": {"id": profile.id, "name": profile.name},
                },
            )

    @app.get("/applications")
    def applications_page(request: Request):
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            # "Applications" is deliberately the jobs you've actually acted
            # on -- everything past the default "new" status -- rather than
            # Jobs' full discovery list. Same underlying data as the Jobs
            # page's status filter pills, viewed as a pipeline instead of a
            # search result: what you're pursuing and where each one stands.
            jobs = (
                session.query(JobPosting)
                .filter(JobPosting.profile_id == profile.id, JobPosting.status != ApplicationStatus.NEW)
                .order_by(JobPosting.status_changed_at.desc())
                .all()
            )
            pending_reminders = (
                session.query(Reminder)
                .filter(Reminder.profile_id == profile.id, Reminder.completed.is_(False))
                .order_by(Reminder.due_at.asc())
                .all()
            )
            reminders_by_job: dict[int, list[Reminder]] = {}
            for reminder in pending_reminders:
                if reminder.job_id is not None:
                    reminders_by_job.setdefault(reminder.job_id, []).append(reminder)

            status_counts_rows = (
                session.query(JobPosting.status, func.count(JobPosting.id))
                .filter(JobPosting.profile_id == profile.id, JobPosting.status != ApplicationStatus.NEW)
                .group_by(JobPosting.status)
                .all()
            )
            counted = {status_value.value: count for status_value, count in status_counts_rows}
            # Ordered by how much a status needs your attention --
            # interviewing/offer first, closed-out rejected/dismissed last
            # -- and shared between the summary pills and the grouped
            # sections below them so the two agree on the story they tell,
            # rather than one following this order and the other the
            # ApplicationStatus enum's declaration order.
            ATTENTION_ORDER = ("interviewing", "offer", "applied", "reviewed", "rejected", "dismissed")
            status_counts = {s: counted.get(s, 0) for s in ATTENTION_ORDER}

            grouped: dict[str, list[dict]] = {s: [] for s in ATTENTION_ORDER}
            for job in jobs:
                grouped.setdefault(job.status.value, []).append({
                    "id": job.id,
                    "title": job.title,
                    "company": job.company,
                    "url": job.url,
                    "status": job.status.value,
                    "status_changed_at": job.status_changed_at,
                    "fit_score": job.fit_score,
                    "reminders": [
                        {"id": r.id, "type": r.type.value, "message": r.message, "due_at": r.due_at}
                        for r in reminders_by_job.get(job.id, [])
                    ],
                })
            return templates.TemplateResponse(
                request=request,
                name="applications.html",
                context={
                    "grouped_applications": {k: v for k, v in grouped.items() if v},
                    "status_counts": status_counts,
                    "total_count": len(jobs),
                    "active_profile": {"id": profile.id, "name": profile.name},
                },
            )

    @app.get("/reminders.ics")
    def export_all_reminders_ics(request: Request):
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            reminders = (
                session.query(Reminder)
                .filter(Reminder.profile_id == profile.id, Reminder.completed.is_(False))
                .order_by(Reminder.due_at.asc())
                .all()
            )
            return PlainTextResponse(
                reminders_to_ics(reminders),
                media_type="text/calendar",
                headers={"Content-Disposition": 'attachment; filename="hanarr-reminders.ics"'},
            )

    @app.get("/reminders/{reminder_id}.ics")
    def export_reminder_ics(request: Request, reminder_id: int):
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            reminder = session.get(Reminder, reminder_id)
            if reminder is None or reminder.profile_id != profile.id:
                return JSONResponse({"error": "Reminder not found."}, status_code=404)
            return PlainTextResponse(
                reminders_to_ics([reminder]),
                media_type="text/calendar",
                headers={"Content-Disposition": f'attachment; filename="hanarr-reminder-{reminder.id}.ics"'},
            )

    @app.post("/jobs/clear")
    def clear_jobs(request: Request):
        # Refuse while a search is writing to the same tables -- clearing
        # mid-search could race with the pipeline's own inserts (delete a
        # row it just added, or leave a partial mix once the search
        # finishes), and afterward the search would still be reporting
        # progress against data that's already been wiped out from under it.
        if state["search_running"]:
            return JSONResponse(
                {"error": "Can't clear jobs while a search is running — stop the search first."},
                status_code=409,
            )

        # Wipes SeenPosting too, not just JobPosting -- otherwise every
        # posting fetched before this point (matched or rejected) would
        # stay permanently skipped by the pipeline's dedup check, and the
        # next search would find nothing "new" even though the dashboard
        # is now empty. This is the escape hatch for exactly that: forcing
        # everything to be re-fetched and re-scored from scratch, e.g.
        # after a scoring-logic change.
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            session.query(Reminder).filter(
                Reminder.profile_id == profile.id, Reminder.job_id.isnot(None)
            ).delete(synchronize_session=False)
            session.query(JobPosting).filter(JobPosting.profile_id == profile.id).delete(
                synchronize_session=False
            )
            session.query(SeenPosting).filter(SeenPosting.profile_id == profile.id).delete(
                synchronize_session=False
            )
            session.commit()
        return RedirectResponse("/", status_code=303)

    @app.post("/search")
    def trigger_search(request: Request):
        if not state["search_running"]:
            next_run_id = state["run_id"] + 1
            profile_id = _active_profile_id(request)
            threading.Thread(
                target=_run_search_in_background, args=(next_run_id, profile_id), daemon=True
            ).start()
        return RedirectResponse("/", status_code=303)

    def _relative_label(delta_seconds: float, future: bool) -> str:
        """Small human-relative label, e.g. "in 5h" or "3h ago". Deliberately
        coarse (minutes/hours/days, not seconds) since search/reminder
        intervals are configured in hours."""
        seconds = abs(delta_seconds)
        if seconds < 3600:
            value, unit = max(1, round(seconds / 60)), "m"
        elif seconds < 86400:
            value, unit = round(seconds / 3600), "h"
        else:
            value, unit = round(seconds / 86400), "d"
        return f"in {value}{unit}" if future else f"{value}{unit} ago"

    def _scheduler_status(profile_id: int | None = None) -> dict:
        """Next-scheduled-run labels, and the last search's persisted
        outcome (which survives a restart, unlike `state`, so this doesn't
        go blank every time "Save & restart Hanarr" is used). `scheduler`
        is only available when `hanarr serve` actually started one (see
        create_app's own docstring); routes that run without it (tests,
        the restart-in-progress moment) just show nothing scheduled rather
        than erroring. APScheduler's next_run_time is timezone-aware
        (local-timezone by default); to_naive_utc normalizes it to match
        this app's naive-UTC convention before comparing against utc_now().
        "Last search" is shown per active profile, since the background
        scheduler runs a cycle for every profile independently."""
        now = utc_now()
        next_search_label = None
        next_reminder_check_label = None
        if scheduler is not None:
            search_job = scheduler.get_job("search")
            if search_job and search_job.next_run_time:
                delta = (to_naive_utc(search_job.next_run_time) - now).total_seconds()
                next_search_label = _relative_label(delta, future=True)
            reminder_job = scheduler.get_job("reminders")
            if reminder_job and reminder_job.next_run_time:
                delta = (to_naive_utc(reminder_job.next_run_time) - now).total_seconds()
                next_reminder_check_label = _relative_label(delta, future=True)

        with session_factory() as session:
            profile = get_active_profile(session, settings, profile_id)
            last_search_label = None
            if profile.last_search_at:
                delta = (now - profile.last_search_at).total_seconds()
                trigger = profile.last_search_trigger or "manual"
                count = profile.last_search_new_count if profile.last_search_new_count is not None else 0
                last_search_label = (
                    f"Last {trigger} search: {count} new posting(s), {_relative_label(delta, future=False)}"
                )

        return {
            "next_search_label": next_search_label,
            "next_reminder_check_label": next_reminder_check_label,
            "last_search_label": last_search_label,
        }

    @app.get("/search/status")
    def search_status(request: Request):
        return JSONResponse(
            {
                "search_running": state["search_running"],
                "run_id": state["run_id"],
                "trigger": state["trigger"],
                "sources_done": state["sources_done"],
                "sources_total": state["sources_total"],
                "current_source": state["current_source"],
                "matched_count": state["matched_count"],
                "considered_total": state["considered_total"],
                "considered_done": state["considered_done"],
                "already_seen_count": state["already_seen_count"],
                "scoring_count": state["scoring_count"],
                "log": state["log"],
                "last_search_result": state["last_search_result"],
                "stop_requested": stop_event.is_set(),
                "llm_paused": state["llm_paused"],
                "llm_paused_message": state["llm_paused_message"],
                **_scheduler_status(_active_profile_id(request)),
            }
        )

    @app.get("/debug/filtered")
    def filtered_postings_debug(request: Request):
        """Shows postings rejected during the most recent search on this
        server process (prefiltered out, or scored below the minimum fit
        score) with the specific reason -- useful for tuning preferences.
        Ephemeral: nothing here is persisted, so it's empty after a
        restart and only ever reflects the last run, not history."""
        return templates.TemplateResponse(
            request=request,
            name="debug_filtered.html",
            context={
                "filtered": list(reversed(state["filtered_log"])),
                "search_running": state["search_running"],
                "active_profile": _active_profile_summary(request),
            },
        )

    @app.post("/search/stop")
    def stop_search():
        if state["search_running"]:
            stop_event.set()
            _log_event({"kind": "info", "text": "Stop requested — finishing the current posting…"})
        return RedirectResponse("/", status_code=303)

    @app.post("/search/resume-now")
    def resume_search_now():
        # Only meaningful while actually paused -- harmless no-op otherwise
        # (the pause loop clears the event right after waking, so setting it
        # with nothing waiting just leaves it set until the next pause, at
        # which point it'd wrongly skip that wait too; guarding here avoids
        # that).
        if state["search_running"] and state["llm_paused"]:
            resume_now_event.set()
        return JSONResponse({"resumed": True})

    @app.post("/api/jobs/rescore")
    def trigger_rescore(request: Request):
        if not rescore_state["running"]:
            rescore_state["running"] = True  # claimed immediately so a double-click can't start two
            next_run_id = rescore_state["run_id"] + 1
            profile_id = _active_profile_id(request)
            threading.Thread(
                target=_run_rescore_in_background, args=(next_run_id, profile_id), daemon=True
            ).start()
        return JSONResponse({"started": True})

    @app.get("/api/jobs/rescore/status")
    def rescore_status():
        return JSONResponse({
            "running": rescore_state["running"],
            "run_id": rescore_state["run_id"],
            "total": rescore_state["total"],
            "rescored": rescore_state["rescored"],
            "log": rescore_state["log"],
            "last_result": rescore_state["last_result"],
            "llm_paused": rescore_state["llm_paused"],
            "llm_paused_message": rescore_state["llm_paused_message"],
            "stop_requested": rescore_stop_event.is_set(),
        })

    @app.post("/api/jobs/rescore/stop")
    def stop_rescore():
        if rescore_state["running"]:
            rescore_stop_event.set()
            _rescore_log_event({"kind": "info", "text": "Stop requested — finishing the current job…"})
        return JSONResponse({"stopped": True})

    @app.post("/api/jobs/rescore/resume-now")
    def resume_rescore_now():
        if rescore_state["running"] and rescore_state["llm_paused"]:
            rescore_resume_now_event.set()
        return JSONResponse({"resumed": True})

    # Backs the dashboard's LLM status light -- a configured LLM
    # (llm.provider != "none") that's reachable shows green; anything else
    # (not configured, unreachable, a bad model/key) shows red rather than
    # guessing. Cached briefly: Ollama's check is cheap (one /api/tags hit)
    # but Anthropic's is a real billed call, and this is meant to be polled
    # by every open dashboard tab.
    _llm_status_cache: dict[str, Any] = {"checked_at": 0.0, "connected": None, "detail": ""}
    LLM_STATUS_CACHE_SECONDS = 30

    @app.api_route("/api/llm/status", methods=["GET", "POST"])
    def llm_status(request: Request):
        # GET polls and respects the cache; POST is the status light being
        # clicked -- an explicit request for a fresh answer right now, so it
        # always bypasses the cache and makes a real check/call.
        now = time.time()
        force = request.method == "POST"
        if not force and now - _llm_status_cache["checked_at"] < LLM_STATUS_CACHE_SECONDS and _llm_status_cache["connected"] is not None:
            return JSONResponse({"connected": _llm_status_cache["connected"], "detail": _llm_status_cache["detail"]})
        if isinstance(market_analysis_llm, NullLLMClient):
            # check_llm_available() treats llm.provider = "none" as a
            # deliberate, valid state (nothing to check) -- correct for
            # deciding whether a search may proceed, but wrong for this
            # light: there IS no LLM, so it must show red/"not connected",
            # not green.
            connected, detail = False, "No LLM configured (llm.provider = \"none\") — jobs are scored with a plain keyword-overlap match instead."
        else:
            try:
                check_llm_available(market_analysis_llm, settings.data_dir)
                connected, detail = True, "LLM is reachable and will be used for job-fit scoring."
            except Exception as exc:  # noqa: BLE001 - any failure means "not known connected"
                connected, detail = False, str(exc)[:300] or "LLM is not configured or not reachable."
        _llm_status_cache.update(checked_at=now, connected=connected, detail=detail)
        return JSONResponse({"connected": connected, "detail": detail})

    @app.post("/remind")
    def trigger_remind(request: Request):
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            due = get_due_reminders(session, profile)
            if due:
                deliver_reminders(due, settings.reminders)
                mark_completed(session, due)
        return RedirectResponse("/", status_code=303)

    @app.get("/config")
    def config_page(request: Request, tab: str = "preferences", saved: str | None = None):
        provider_diagnostics = _provider_diagnostics()
        preferences = _active_effective_preferences(request)
        resolved_tab = tab if tab in {"preferences", "app", "schedule", "updates"} else "preferences"
        return templates.TemplateResponse(
            request=request,
            name="config.html",
            context={
                "settings": settings,
                "preferences": preferences,
                "active_tab": resolved_tab,
                "saved": saved == "1",
                "errors": [],
                "provider_diagnostics": provider_diagnostics,
                "agent_recommendations": _agent_recommendations(provider_diagnostics),
                "resume_upload": _resume_upload_status(_active_profile_id(request)),
                "active_profile": _active_profile_summary(request),
                "onboarding": _onboarding_status_for_request(request),
                "local_timezone": str(tzlocal.get_localzone()),
                "common_dealbreakers": COMMON_DEALBREAKERS,
                "dealbreakers_custom": custom_dealbreakers(preferences.dealbreakers),
                "config_version": _config_version_for_tab(resolved_tab, request),
                "app_version": APP_VERSION,
            },
        )

    def _provider_diagnostics():
        return detect_ollama(settings.llm.model, settings.llm.base_url, settings.data_dir)

    def _agent_recommendations(diagnostics):
        """Per-role model suggestion plus the current override (if any) for
        the Settings > App "Per-task model sizing" section -- see
        ollama_setup.recommend_model_for_role for why this isn't just
        "lighter for everything that isn't the main model."""
        return {
            role: {
                "current_override": getattr(settings.agents, role).model,
                "recommendation": recommend_model_for_role(role, diagnostics.hardware),
            }
            for role in ("profiler", "market_analysis", "curriculum", "evaluator", "resume_writer", "coach")
        }

    def _active_profile_summary(request: Request) -> dict:
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            return {"id": profile.id, "name": profile.name}

    def _onboarding_status_for_request(request: Request) -> dict:
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            return _onboarding_status(session, profile, settings)

    def _config_version_for_tab(tab: str, request: Request) -> int:
        """The Preferences tab's stale-save check must compare against the
        active profile's own preferences_version, not the shared
        config.yaml config_version -- otherwise one profile saving would
        spuriously block every other profile's unrelated save. The other
        three tabs (app/schedule/updates) still edit shared config.yaml,
        so they keep using the global counter."""
        if tab == "preferences":
            with session_factory() as session:
                profile = get_active_profile(session, settings, _active_profile_id(request))
                return profile.preferences_version
        return config_version["value"]

    def _active_effective_preferences(request: Request):
        """The active profile's own saved match criteria, or the shared
        config.yaml value as a live fallback -- see config.effective_preferences.
        Used to render the Preferences tab's form fields, which show a
        specific profile's values now rather than always the shared ones."""
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            return effective_preferences(profile, settings)

    def _resume_upload_status(profile_id: int | None = None) -> dict:
        """For server-rendered template context; parsed_at is a raw datetime
        here so the template can format it, unlike the JSON-facing
        /config/resume/status route which sends an ISO string instead."""
        with session_factory() as session:
            profile = get_active_profile(session, settings, profile_id)
            return {
                "original_filename": profile.resume_original_filename,
                "parsed_at": profile.resume_parsed_at,
            }

    @app.get("/config/provider/status")
    def provider_status():
        """Read-only local provider diagnostics; never includes API secrets."""
        return JSONResponse(_provider_diagnostics().to_dict())

    @app.post("/config/provider/setup")
    async def provider_setup(request: Request):
        """Offer or perform one explicitly consented, bounded setup action."""
        form = await request.form()
        action = str(form.get("action", "")).strip()
        consent = str(form.get("consent", "")).lower() in {"1", "true", "yes", "on"}
        diagnostics = _provider_diagnostics()

        if action == "ollama_installer":
            if diagnostics.executable_path:
                return JSONResponse({"status": "already_installed", "diagnostics": diagnostics.to_dict()})
            destination = settings.data_dir / "setup" / "OllamaSetup.exe"
            offer = installer_offer(destination)
            if not consent:
                return JSONResponse({"status": "consent_required", "offer": offer.to_dict()})
            try:
                staged = stage_ollama_installer(destination, consent=True)
            except SetupError as exc:
                logger.warning("Ollama installer staging failed: %s", exc)
                return JSONResponse({"status": "error", "error": str(exc), "offer": offer.to_dict()}, status_code=502)
            return JSONResponse({"status": "staged", "destination": str(staged), "message": "Installer staged but not started. Run it yourself after reviewing it."})

        return JSONResponse({"status": "error", "error": "Unknown setup action."}, status_code=400)

    @app.post("/config/provider/pull")
    async def start_model_pull(request: Request):
        """Starts a background model download -- see pull_state/_pull_model_in_background.
        Still requires an explicit request from a button click; never starts on its own."""
        form = await request.form()
        model = str(form.get("model", "")).strip()
        if not model:
            return JSONResponse({"error": "A model name is required."}, status_code=400)
        if pull_state["running"] and not task_is_stuck(pull_state, MODEL_PULL_STUCK_SECONDS):
            return JSONResponse({"error": "A model download is already in progress."}, status_code=409)
        diagnostics = _provider_diagnostics()
        if not diagnostics.service_reachable:
            return JSONResponse({"error": "Ollama is not reachable; start Ollama and try again."}, status_code=409)
        next_run_id = pull_state["run_id"] + 1
        threading.Thread(
            target=_pull_model_in_background, args=(next_run_id, model), daemon=True
        ).start()
        return JSONResponse({"run_id": next_run_id})

    @app.get("/config/provider/pull/status")
    def model_pull_status():
        return JSONResponse(
            {
                "running": pull_state["running"],
                "run_id": pull_state["run_id"],
                "model": pull_state["model"],
                "phase": pull_state["phase"],
                "percent": pull_state["percent"],
                "error": pull_state["error"],
            }
        )

    @app.post("/config/provider/pull/cancel")
    def cancel_model_pull():
        if pull_state["running"]:
            pull_cancel_event.set()
        return JSONResponse({"cancelling": pull_state["running"]})

    @app.get("/config/update/check")
    def update_check():
        """Check configured release metadata; never downloads or installs."""
        try:
            return JSONResponse(check_for_update(settings))
        except UpdateCheckError as exc:
            logger.warning("Update check failed: %s", exc)
            return JSONResponse({"status": "error", "error": str(exc)}, status_code=502)

    @app.post("/config/update/install")
    async def update_install(request: Request):
        """Manual, explicit install: the same approval boundary as before,
        now actually implemented. Checks again, downloads and verifies the
        installer's checksum, then applies it immediately -- this is the
        deliberate manual fallback for anyone who cancelled an auto-update
        countdown, or who has updates.auto_update off and only wants to
        install on demand. Synchronous: the request blocks until the
        (few-hundred-MB) download finishes, the same trade-off the Ollama
        installer download already has."""
        form = await request.form()
        consent = str(form.get("consent", "")).lower() in {"1", "true", "yes", "on"}
        if not consent:
            return JSONResponse(
                {"status": "approval_required", "message": "Review release metadata and explicitly approve before downloading or installing."},
                status_code=409,
            )
        if state["search_running"]:
            return JSONResponse(
                {"status": "error", "message": "A search is running — try again once it finishes."},
                status_code=409,
            )
        if not self_update.is_packaged_build():
            return JSONResponse(
                {"status": "error", "message": "Only the installed app can apply an update; not available when running from source."},
                status_code=400,
            )

        try:
            result = check_for_update(settings)
        except UpdateCheckError as exc:
            return JSONResponse({"status": "error", "message": str(exc)}, status_code=502)
        if result.get("status") != "update_available":
            return JSONResponse({"status": result.get("status", "up_to_date"), "message": "Already up to date."})

        release_dict = result["release"]
        release = self_update.release_from_check_result(release_dict)
        asset = self_update.find_windows_installer_asset(release)
        if asset is None:
            return JSONResponse(
                {"status": "error", "message": "This release has no Windows installer asset."}, status_code=502,
            )

        destination = settings.data_dir / "updates" / asset.name
        try:
            installer_path = self_update.download_and_verify_installer(asset, destination)
        except self_update.SelfUpdateError as exc:
            return JSONResponse({"status": "error", "message": str(exc)}, status_code=502)

        upd_state["version"] = release_dict["version"]
        upd_state["installer_path"] = str(installer_path)
        upd_state["applying"] = True
        try:
            self_update.apply_update(installer_path)
        except self_update.SelfUpdateError as exc:
            upd_state["applying"] = False
            upd_state["error"] = str(exc)[:300]
            return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)

        return JSONResponse({"status": "applying", "version": release_dict["version"]})

    @app.get("/update/status")
    def update_status():
        """Polled by the site-wide update banner. Deliberately excludes
        installer_path (a local filesystem path -- no reason to expose it
        to the page)."""
        return JSONResponse({
            "available": upd_state["available"],
            "version": upd_state["version"],
            "notes_url": upd_state["notes_url"],
            "apply_at": upd_state["apply_at"],
            "cancelled": upd_state["cancelled"],
            "applying": upd_state["applying"],
            "error": upd_state["error"],
        })

    @app.post("/update/cancel")
    def cancel_update():
        """Stops the countdown from auto-applying. Deliberately leaves the
        already-downloaded, checksum-verified installer staged and
        `available` true -- Settings' "Install now" (see update_install
        above) is the standing manual fallback for applying it later,
        exactly the same installer, without re-downloading."""
        if upd_state["available"]:
            upd_state["cancelled"] = True
        return JSONResponse({"cancelled": upd_state["cancelled"]})

    async def _handle_config_post(request: Request, tab: str, apply_fn):
        # Autosave (Preferences fields saving on blur/tab-away, see
        # config.html's autosave script) posts the same form to the same
        # route with this header set, and wants a small JSON ack/error list
        # back instead of a full re-rendered page or a redirect -- neither
        # of which the background fetch() call does anything useful with.
        is_autosave = request.headers.get("x-autosave") == "1"

        form = await request.form()
        form_dict = {k: v for k, v in form.items()}

        # Optimistic concurrency: the page this form came from rendered a
        # snapshot of config_version. If the live value has since moved on
        # (another tab saved, a resume upload auto-populated preferences,
        # etc.), this form's untouched fields reflect a settings state that
        # no longer exists -- applying it would silently revert or blank
        # out whatever changed in the meantime. Refuse instead, rather than
        # trusting a snapshot that's provably out of date.
        submitted_version = form_dict.get("config_version")
        current_version = _config_version_for_tab(tab, request)
        if submitted_version is not None and submitted_version != str(current_version):
            conflict_message = (
                "Settings changed elsewhere since this page loaded (another tab, a resume "
                "upload, etc.) — reload the page to see the latest before saving again."
            )
            if is_autosave:
                return JSONResponse({"saved": False, "conflict": True, "errors": [conflict_message]}, status_code=409)
            conflict_preferences = _active_effective_preferences(request)
            conflict_diagnostics = _provider_diagnostics()
            return templates.TemplateResponse(
                request=request,
                name="config.html",
                context={
                    "settings": settings,
                    "preferences": conflict_preferences,
                    "active_tab": tab,
                    "saved": False,
                    "errors": [conflict_message],
                    "provider_diagnostics": conflict_diagnostics,
                    "agent_recommendations": _agent_recommendations(conflict_diagnostics),
                    "resume_upload": _resume_upload_status(_active_profile_id(request)),
                    "active_profile": _active_profile_summary(request),
                    "onboarding": _onboarding_status_for_request(request),
                    "local_timezone": str(tzlocal.get_localzone()),
                    "common_dealbreakers": COMMON_DEALBREAKERS,
                    "dealbreakers_custom": custom_dealbreakers(conflict_preferences.dealbreakers),
                    "config_version": current_version,
                    "app_version": APP_VERSION,
                },
                status_code=409,
            )

        current = settings_to_dict(settings)
        updated_dict = apply_fn(current, form_dict)
        new_settings, errors = validate_and_build(updated_dict)

        if errors:
            if is_autosave:
                return JSONResponse({"saved": False, "errors": errors}, status_code=400)
            error_preferences = _active_effective_preferences(request)
            error_diagnostics = _provider_diagnostics()
            return templates.TemplateResponse(
                request=request,
                name="config.html",
                context={
                    "settings": settings,
                    "preferences": error_preferences,
                    "active_tab": tab,
                    "saved": False,
                    "errors": errors,
                    "provider_diagnostics": error_diagnostics,
                    "agent_recommendations": _agent_recommendations(error_diagnostics),
                    "resume_upload": _resume_upload_status(_active_profile_id(request)),
                    "active_profile": _active_profile_summary(request),
                    "onboarding": _onboarding_status_for_request(request),
                    "local_timezone": str(tzlocal.get_localzone()),
                    "common_dealbreakers": COMMON_DEALBREAKERS,
                    "dealbreakers_custom": custom_dealbreakers(error_preferences.dealbreakers),
                    "config_version": current_version,
                    "app_version": APP_VERSION,
                },
            )

        # Match criteria (preferences.*) are per-profile, not shared -- see
        # config.effective_preferences. Persisted onto the active profile
        # instead of copied onto the live settings/config.yaml, which is
        # what "forks" a profile away from the shared default the first
        # time its Preferences tab is ever saved. Everything else on this
        # same form (sources.*, matching.min_fit_score) stays shared, same
        # as before.
        new_preferences_version: int | None = None
        if tab == "preferences":
            with session_factory() as session:
                profile = get_active_profile(session, settings, _active_profile_id(request))
                backup_profile_preferences(settings, profile.id, profile.preferences_json)
                profile.preferences_json = new_settings.preferences.model_dump_json()
                profile.preferences_version += 1
                session.commit()
                new_preferences_version = profile.preferences_version

        # Mutate the live settings object in place — the scheduler, pipeline,
        # and this app all hold a reference to the same instance, so
        # matching/source changes apply on the next search without a
        # restart. LLM/schedule/dashboard changes are saved but only take
        # effect after `hanarr serve` is restarted, since the LLM client
        # and scheduler intervals are already built from the old values —
        # the config page says so next to those fields.
        for field in Settings.model_fields:
            if field == "data_dir" or (field == "preferences" and tab == "preferences"):
                continue
            setattr(settings, field, getattr(new_settings, field))

        save_settings_to_yaml(settings, str(DEFAULT_CONFIG_PATH))
        persist_secrets_from_form(form_dict)
        # The global counter still advances even on a preferences-tab save
        # -- sources.*/matching.* on that same form are shared config.yaml
        # fields other tabs' stale-save checks must still see as changed.
        # The version handed back to THIS form, though, is whichever one
        # its own next save will actually be checked against.
        config_version["value"] += 1
        response_version = new_preferences_version if new_preferences_version is not None else config_version["value"]
        if is_autosave:
            return JSONResponse({"saved": True, "config_version": response_version})
        return RedirectResponse(f"/config?tab={tab}&saved=1", status_code=303)

    @app.post("/config/suggest-keywords")
    def trigger_suggest_keywords(request: Request):
        if not keyword_state["running"] or _task_is_stuck(keyword_state):
            next_run_id = keyword_state["run_id"] + 1
            profile_id = _active_profile_id(request)
            threading.Thread(
                target=_suggest_keywords_in_background, args=(next_run_id, profile_id), daemon=True
            ).start()
            return JSONResponse({"run_id": next_run_id})
        return JSONResponse({"run_id": keyword_state["run_id"]})

    @app.get("/config/suggest-keywords/status")
    def suggest_keywords_status():
        return JSONResponse(
            {
                "running": keyword_state["running"],
                "run_id": keyword_state["run_id"],
                "keywords": keyword_state["keywords"],
                "error": keyword_state["error"],
            }
        )

    @app.post("/config/resume")
    async def upload_resume(request: Request, file: UploadFile = File(...)):
        if resume_state["running"] and not _task_is_stuck(resume_state):
            return JSONResponse({"error": "A resume is already being parsed — wait for it to finish."}, status_code=409)

        original_name = file.filename or ""
        ext = Path(original_name).suffix.lower()
        if ext not in ALLOWED_RESUME_EXTENSIONS:
            allowed = ", ".join(sorted(ALLOWED_RESUME_EXTENSIONS))
            return JSONResponse({"error": f"Unsupported file type {ext!r} — allowed: {allowed}"}, status_code=400)

        with session_factory() as session:
            active_profile = get_active_profile(session, settings, _active_profile_id(request))
            default_profile = get_or_create_profile(session, settings)
            profile_id = active_profile.id
            is_default_profile = active_profile.id == default_profile.id

        # Each profile's upload lives in its own subdirectory -- otherwise
        # two profiles' resumes would collide on the same fixed filename and
        # silently overwrite each other on disk.
        profile_resumes_dir = RESUMES_DIR / str(profile_id)
        profile_resumes_dir.mkdir(parents=True, exist_ok=True)
        # Fixed filename per extension rather than keeping the upload's
        # original name — one resume per profile, so each new upload
        # replaces that profile's last rather than accumulating files.
        dest = profile_resumes_dir / f"resume{ext}"
        staged = profile_resumes_dir / f".resume-upload{ext}.staging"
        written = 0
        try:
            too_large = False
            with staged.open("wb") as output:
                while True:
                    chunk = await file.read(UPLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > MAX_RESUME_BYTES:
                        too_large = True
                        break
                    output.write(chunk)
            if too_large:
                staged.unlink(missing_ok=True)
                return JSONResponse(
                    {"error": f"resume upload cannot exceed {MAX_RESUME_BYTES} bytes."},
                    status_code=413,
                )
            staged.replace(dest)
        except OSError as exc:
            logger.exception("Resume upload could not be saved")
            staged.unlink(missing_ok=True)
            return JSONResponse({"error": "Could not save the resume upload; check server logs."}, status_code=400)

        # Only the default profile's upload updates the shared config.yaml
        # path -- a secondary profile's file lives at its own path above and
        # would otherwise silently become everyone's global default.
        if is_default_profile:
            settings.profile.resume_path = str(dest)
            save_settings_to_yaml(settings, str(DEFAULT_CONFIG_PATH))

        next_run_id = resume_state["run_id"] + 1
        threading.Thread(
            target=_reparse_resume_in_background,
            args=(next_run_id, original_name, profile_id, dest, is_default_profile),
            daemon=True,
        ).start()
        return JSONResponse({"run_id": next_run_id, "saved_as": str(dest)})

    @app.post("/config/resume/reparse")
    def retry_resume_extraction_route(request: Request):
        if resume_state["running"] and not _task_is_stuck(resume_state):
            return JSONResponse({"error": "A resume is already being parsed — wait for it to finish."}, status_code=409)
        profile_id = _active_profile_id(request)
        with session_factory() as session:
            profile = get_active_profile(session, settings, profile_id)
            if not profile.resume_text:
                return JSONResponse({"error": "No resume has been uploaded yet for this profile."}, status_code=400)
        next_run_id = resume_state["run_id"] + 1
        threading.Thread(
            target=_retry_resume_extraction_in_background, args=(next_run_id, profile_id), daemon=True,
        ).start()
        return JSONResponse({"run_id": next_run_id})

    @app.get("/config/resume/status")
    def resume_status(request: Request):
        with session_factory() as session:
            profile = get_active_profile(session, settings, _active_profile_id(request))
            original_filename = profile.resume_original_filename
            parsed_at = profile.resume_parsed_at.isoformat() if profile.resume_parsed_at else None
        return JSONResponse(
            {
                "running": resume_state["running"],
                "run_id": resume_state["run_id"],
                "result": resume_state["result"],
                "error": resume_state["error"],
                "resume_path": settings.profile.resume_path,
                "resume_original_filename": original_filename,
                "resume_parsed_at": parsed_at,
            }
        )

    @app.post("/config/preferences")
    async def save_preferences(request: Request):
        return await _handle_config_post(request, "preferences", apply_preferences_form)

    @app.post("/config/app")
    async def save_app_config(request: Request):
        return await _handle_config_post(request, "app", apply_app_config_form)

    @app.post("/config/schedule")
    async def save_schedule_reminders(request: Request):
        return await _handle_config_post(request, "schedule", apply_schedule_reminders_form)

    @app.post("/config/updates")
    async def save_updates(request: Request):
        return await _handle_config_post(request, "updates", apply_updates_form)

    def _shutdown_and_reexec():
        # A background LLM call (resume re-parse, keyword suggestion) has no
        # cooperative stop hook the way search does, so it's not "gracefully"
        # stopped here — it's simply killed along with the rest of the
        # process, which is the correct behavior for a restart. Search does
        # get a cooperative stop signal first since it checks should_stop
        # between postings and can wind down cleanly in that brief window.
        if state["search_running"]:
            stop_event.set()
        if scheduler is not None:
            try:
                scheduler.shutdown(wait=False)
            except Exception:  # noqa: BLE001
                logger.exception("Scheduler shutdown failed during restart")
        # os.execv does NOT provide true process replacement on Windows,
        # despite its docstring: there is no exec syscall, so the C runtime
        # emulates it by spawning a child that inherits this process's open
        # handles -- including the listening socket -- and then blocking
        # this process until that child exits. The child's own bind attempt
        # then collides with this process still holding the port, and
        # neither side recovers: this process hangs inside the emulated
        # exec forever, unresponsive to every request, which is exactly
        # what happened when this was reported as a hung server.
        #
        # subprocess.Popen does not inherit handles by default, so it
        # spawns a genuinely independent process instead. Exit immediately
        # afterward (os._exit, not sys.exit -- skip Python's normal
        # shutdown sequence entirely rather than risk it blocking on a
        # non-daemon thread) so the port is actually released; the new
        # process's own startup absorbs the brief remaining gap by
        # retrying its bind (see launch.py's _wait_for_port_available).
        subprocess.Popen([sys.argv[0], *sys.argv[1:]], close_fds=True)
        os._exit(0)

    @app.post("/restart")
    def restart_server():
        # Delay the actual re-exec until after this response has had a
        # moment to reach the browser — doing it inline would kill the
        # process (and the socket) before the client ever sees a reply.
        threading.Timer(0.5, _shutdown_and_reexec).start()
        return JSONResponse({"restarting": True})

    return app
