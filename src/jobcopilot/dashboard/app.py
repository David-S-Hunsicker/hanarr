"""Minimal read/write dashboard: view matched jobs and reminders, update
a job's status, and trigger a search or reminder check on demand.
Deliberately server-rendered (Jinja2, a few forms) rather than a JS
framework — keeps the project approachable to contribute to and easy to
self-host with nothing but `jobcopilot serve`.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func

from ..config import DEFAULT_CONFIG_PATH, Settings
from ..db import get_or_create_profile, make_session_factory
from ..llm import build_llm_client
from ..coaching_projects import create_coaching_project, project_status
from ..models import ApplicationStatus, JobPosting, Project, ProjectMode, Reminder, SeenPosting
from ..pipeline import run_search_cycle
from ..reminders import deliver_reminders, get_due_reminders, mark_completed
from ..resume import ALLOWED_RESUME_EXTENSIONS, parse_and_store_resume, suggest_boost_keywords
from ..skill_analysis import analyze_job, saved_job_gap, saved_job_gaps
from .config_form import (
    apply_app_config_form,
    apply_preferences_form,
    apply_schedule_reminders_form,
    save_settings_to_yaml,
    settings_to_dict,
    validate_and_build,
)

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"

# A wedged LLM call (Ollama hung, unreachable, or just very slow on a long
# prompt) would otherwise leave a background task's "running" flag stuck
# true forever, locking out all future attempts with no recovery short of
# restarting the server. Any task whose "running" flag has been set longer
# than the configured LLM timeout plus this buffer is treated as dead — the
# ceiling is generous on purpose, since a false "not stuck" verdict just
# means a redundant background thread, while a false "stuck" verdict would
# let two re-parses race on the same profile.
STUCK_TASK_BUFFER_SECONDS = 30.0


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
    now = now if now is not None else dt.datetime.utcnow()
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
    now = now if now is not None else dt.datetime.utcnow()
    delta_seconds = (now - posted_at).total_seconds()
    return 0 <= delta_seconds < within_days * 86400


def create_app(settings: Settings, scheduler: Any = None) -> FastAPI:
    """`scheduler` is the BackgroundScheduler from start_scheduler(), passed
    through so the restart route can shut it down cleanly before
    re-executing the process. Optional — tests and other callers that don't
    run the scheduler can omit it; the restart route just skips that step."""
    app = FastAPI(title="Hannar")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.cache = None
    templates.env.filters["posting_age"] = format_posting_age
    templates.env.filters["is_recent_posting"] = is_recent_posting
    session_factory = make_session_factory(settings)
    llm = build_llm_client(settings.llm)

    # Simple in-memory state so the page can show live progress without a
    # job queue — this dashboard is single-user, single-process, so a plain
    # dict plus a bounded activity log is enough. `run_id` lets the browser
    # tell "still the same run" apart from "a new one started" across polls.
    MAX_LOG_ENTRIES = 25
    state = {
        "search_running": False,
        "run_id": 0,
        "sources_done": 0,
        "sources_total": 0,
        "current_source": None,
        "matched_count": 0,
        # considered_total grows as each source's fetch completes (we don't
        # know the grand total upfront — sources are fetched one at a
        # time); considered_done counts postings actually looked at
        # (skipped or scored, either way) toward that running total.
        "considered_total": 0,
        "considered_done": 0,
        "log": [],
        "last_search_result": None,
    }
    stop_event = threading.Event()

    def _log_event(entry: dict) -> None:
        entry["at"] = time.time()
        state["log"].append(entry)
        if len(state["log"]) > MAX_LOG_ENTRIES:
            state["log"] = state["log"][-MAX_LOG_ENTRIES:]

    def _on_progress(event: dict) -> None:
        kind = event["event"]
        if kind == "source_start":
            state["current_source"] = event["source"]
            _log_event({"kind": "source_start", "text": f"Searching {event['source']}…"})
        elif kind == "source_fetched":
            state["considered_total"] += event["count"]
            _log_event({"kind": "info", "text": f"{event['source']}: {event['count']} posting(s) fetched"})
        elif kind == "source_error":
            _log_event({"kind": "error", "text": f"{event['source']}: fetch failed, skipping"})
        elif kind == "scoring":
            _log_event({"kind": "scoring", "text": f"Scoring: {event['title']} at {event['company']}"})
        elif kind == "considered":
            state["considered_done"] += 1
        elif kind == "matched":
            state["matched_count"] += 1
            score = event["fit_score"]
            _log_event(
                {
                    "kind": "matched",
                    "text": f"Matched ({score:.0f}): {event['title']} at {event['company']}",
                }
            )
        elif kind == "source_done":
            state["sources_done"] += 1
            state["current_source"] = None
        elif kind == "complete":
            _log_event({"kind": "done", "text": f"Search complete — {event['new_count']} new posting(s)."})
        elif kind == "stopped":
            _log_event({"kind": "error", "text": f"Search stopped — {event['new_count']} new posting(s) kept."})

    def _run_search_in_background(run_id: int):
        state["search_running"] = True
        state["run_id"] = run_id
        state["sources_done"] = 0
        state["sources_total"] = len(settings.sources.__class__.model_fields)
        state["current_source"] = None
        state["matched_count"] = 0
        state["considered_total"] = 0
        state["considered_done"] = 0
        state["log"] = []
        stop_event.clear()
        try:
            from ..connectors import build_enabled_connectors

            state["sources_total"] = len(build_enabled_connectors(settings.sources))
            with session_factory() as session:
                profile = get_or_create_profile(session, settings)
                n = run_search_cycle(
                    session, settings, profile, llm,
                    on_progress=_on_progress,
                    should_stop=stop_event.is_set,
                )
                if stop_event.is_set():
                    state["last_search_result"] = f"Search stopped — {n} new posting(s) kept."
                else:
                    state["last_search_result"] = f"{n} new posting(s) matched and stored."
        except Exception:  # noqa: BLE001
            logger.exception("Manual search cycle failed")
            state["last_search_result"] = "Search failed — check server logs."
            _log_event({"kind": "error", "text": "Search failed — check server logs."})
        finally:
            state["search_running"] = False
            stop_event.clear()

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

    def _suggest_keywords_in_background(run_id: int):
        keyword_state["running"] = True
        keyword_state["run_id"] = run_id
        keyword_state["keywords"] = None
        keyword_state["error"] = None
        keyword_state["started_at"] = time.time()
        try:
            with session_factory() as session:
                profile = get_or_create_profile(session, settings)
                if not profile.resume_text:
                    keyword_state["error"] = "No resume text on file — run `jobcopilot init` first."
                    return
                keywords = suggest_boost_keywords(
                    profile.resume_text, settings.preferences.target_titles, llm
                )
                if not keywords:
                    keyword_state["error"] = "The model didn't return any keywords — try again."
                else:
                    keyword_state["keywords"] = keywords
        except Exception:  # noqa: BLE001
            logger.exception("Keyword suggestion failed")
            keyword_state["error"] = "Suggestion failed — check server logs (is the LLM reachable?)."
        finally:
            keyword_state["running"] = False

    # Same background-thread pattern again: saving the upload is instant,
    # but the re-parse is an LLM call.
    RESUMES_DIR = Path("resumes")
    resume_state = {
        "running": False,
        "run_id": 0,
        "result": None,
        "error": None,
        "started_at": None,
    }

    def _reparse_resume_in_background(run_id: int):
        resume_state["running"] = True
        resume_state["run_id"] = run_id
        resume_state["result"] = None
        resume_state["error"] = None
        resume_state["started_at"] = time.time()
        try:
            with session_factory() as session:
                profile, summary, prefs_changed = parse_and_store_resume(session, settings, llm)

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

    @app.get("/")
    def index(request: Request, status: str | None = None):
        with session_factory() as session:
            profile = get_or_create_profile(session, settings)
            query = session.query(JobPosting).filter(JobPosting.profile_id == profile.id)
            if status:
                query = query.filter(JobPosting.status == ApplicationStatus(status))
            jobs = query.order_by(JobPosting.fit_score.desc()).all()
            reminders = get_due_reminders(session, profile)

            # "Considered" = every posting fetched and scored (matched or
            # not), regardless of the current status filter above — this is
            # a standing total, not affected by which status tab is open.
            considered_count = (
                session.query(SeenPosting).filter(SeenPosting.profile_id == profile.id).count()
            )
            matched_count = (
                session.query(JobPosting).filter(JobPosting.profile_id == profile.id).count()
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

            return templates.TemplateResponse(
                request=request,
                name="index.html",
                context={
                    "jobs": jobs,
                    "reminders": reminders,
                    "statuses": [s.value for s in ApplicationStatus],
                    "current_filter": status or "",
                    "search_running": state["search_running"],
                    "last_search_result": state["last_search_result"],
                    "run_id": state["run_id"],
                    "considered_count": considered_count,
                    "matched_count": matched_count,
                    "status_counts": status_counts,
                    "gap_by_job": gap_by_job,
                    "projects": [project_status(project) for project in projects],
                },
            )

    @app.post("/jobs/{job_id}/status")
    def update_status(job_id: int, new_status: str = Form(...)):
        with session_factory() as session:
            profile = get_or_create_profile(session, settings)
            job = session.get(JobPosting, job_id)
            if job and job.profile_id == profile.id:
                job.status = ApplicationStatus(new_status)
                session.commit()
        return RedirectResponse("/", status_code=303)

    @app.post("/api/jobs/{job_id}/skill-gaps/analyze")
    def analyze_job_skill_gaps(job_id: int):
        with session_factory() as session:
            profile = get_or_create_profile(session, settings)
            job = session.get(JobPosting, job_id)
            if job is None or job.profile_id != profile.id:
                return JSONResponse({"error": "Saved job not found."}, status_code=404)
            result = analyze_job(session, profile, job, llm)
            session.commit()
            return JSONResponse(result)

    @app.get("/api/jobs/{job_id}/skill-gaps")
    def get_job_skill_gaps(job_id: int):
        with session_factory() as session:
            profile = get_or_create_profile(session, settings)
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

    @app.get("/api/skill-gaps")
    def get_skill_gaps():
        with session_factory() as session:
            profile = get_or_create_profile(session, settings)
            return JSONResponse({"jobs": saved_job_gaps(session, profile)})

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
            profile = get_or_create_profile(session, settings)
            try:
                result = create_coaching_project(
                    session, profile.id, mode, llm, job_id=job_id, skill_id=skill_id
                )
                session.commit()
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            return JSONResponse(result, status_code=201)

    @app.get("/api/coaching-projects")
    def list_projects():
        with session_factory() as session:
            profile = get_or_create_profile(session, settings)
            projects = (
                session.query(Project)
                .filter(Project.profile_id == profile.id)
                .order_by(Project.id.desc())
                .all()
            )
            return JSONResponse({"projects": [project_status(project) for project in projects]})

    @app.get("/api/coaching-projects/{project_id}")
    def get_project(project_id: int):
        with session_factory() as session:
            profile = get_or_create_profile(session, settings)
            project = session.get(Project, project_id)
            if project is None or project.profile_id != profile.id:
                return JSONResponse({"error": "Coaching project not found."}, status_code=404)
            return JSONResponse(project_status(project))

    @app.post("/jobs/clear")
    def clear_jobs():
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
            profile = get_or_create_profile(session, settings)
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
    def trigger_search():
        if not state["search_running"]:
            next_run_id = state["run_id"] + 1
            threading.Thread(target=_run_search_in_background, args=(next_run_id,), daemon=True).start()
        return RedirectResponse("/", status_code=303)

    @app.get("/search/status")
    def search_status():
        return JSONResponse(
            {
                "search_running": state["search_running"],
                "run_id": state["run_id"],
                "sources_done": state["sources_done"],
                "sources_total": state["sources_total"],
                "current_source": state["current_source"],
                "matched_count": state["matched_count"],
                "considered_total": state["considered_total"],
                "considered_done": state["considered_done"],
                "log": state["log"],
                "last_search_result": state["last_search_result"],
                "stop_requested": stop_event.is_set(),
            }
        )

    @app.post("/search/stop")
    def stop_search():
        if state["search_running"]:
            stop_event.set()
            _log_event({"kind": "info", "text": "Stop requested — finishing the current posting…"})
        return RedirectResponse("/", status_code=303)

    @app.post("/remind")
    def trigger_remind():
        with session_factory() as session:
            profile = get_or_create_profile(session, settings)
            due = get_due_reminders(session, profile)
            if due:
                deliver_reminders(due, settings.reminders)
                mark_completed(session, due)
        return RedirectResponse("/", status_code=303)

    @app.get("/config")
    def config_page(request: Request, tab: str = "preferences", saved: str | None = None):
        return templates.TemplateResponse(
            request=request,
            name="config.html",
            context={
                "settings": settings,
                "active_tab": tab if tab in {"preferences", "app", "schedule"} else "preferences",
                "saved": saved == "1",
                "errors": [],
            },
        )

    async def _handle_config_post(request: Request, tab: str, apply_fn):
        form = await request.form()
        form_dict = {k: v for k, v in form.items()}
        current = settings_to_dict(settings)
        updated_dict = apply_fn(current, form_dict)
        new_settings, errors = validate_and_build(updated_dict)

        if errors:
            return templates.TemplateResponse(
                request=request,
                name="config.html",
                context={
                    "settings": settings,
                    "active_tab": tab,
                    "saved": False,
                    "errors": errors,
                },
            )

        # Mutate the live settings object in place — the scheduler, pipeline,
        # and this app all hold a reference to the same instance, so
        # preference/matching/source changes apply on the next search
        # without a restart. LLM/schedule/dashboard changes are saved but
        # only take effect after `jobcopilot serve` is restarted, since the
        # LLM client and scheduler intervals are already built from the old
        # values — the config page says so next to those fields.
        for field in Settings.model_fields:
            if field == "data_dir":
                continue
            setattr(settings, field, getattr(new_settings, field))

        save_settings_to_yaml(settings, str(DEFAULT_CONFIG_PATH))
        return RedirectResponse(f"/config?tab={tab}&saved=1", status_code=303)

    @app.post("/config/suggest-keywords")
    def trigger_suggest_keywords():
        if not keyword_state["running"] or _task_is_stuck(keyword_state):
            next_run_id = keyword_state["run_id"] + 1
            threading.Thread(
                target=_suggest_keywords_in_background, args=(next_run_id,), daemon=True
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
    async def upload_resume(file: UploadFile = File(...)):
        if resume_state["running"] and not _task_is_stuck(resume_state):
            return JSONResponse({"error": "A resume is already being parsed — wait for it to finish."}, status_code=409)

        original_name = file.filename or ""
        ext = Path(original_name).suffix.lower()
        if ext not in ALLOWED_RESUME_EXTENSIONS:
            allowed = ", ".join(sorted(ALLOWED_RESUME_EXTENSIONS))
            return JSONResponse({"error": f"Unsupported file type {ext!r} — allowed: {allowed}"}, status_code=400)

        contents = await file.read()
        RESUMES_DIR.mkdir(parents=True, exist_ok=True)
        # Fixed filename per extension rather than keeping the upload's
        # original name — one resume per instance, so each new upload
        # replaces the last rather than accumulating files.
        dest = RESUMES_DIR / f"resume{ext}"
        dest.write_bytes(contents)

        settings.profile.resume_path = str(dest)
        save_settings_to_yaml(settings, str(DEFAULT_CONFIG_PATH))

        next_run_id = resume_state["run_id"] + 1
        threading.Thread(target=_reparse_resume_in_background, args=(next_run_id,), daemon=True).start()
        return JSONResponse({"run_id": next_run_id, "saved_as": str(dest)})

    @app.get("/config/resume/status")
    def resume_status():
        return JSONResponse(
            {
                "running": resume_state["running"],
                "run_id": resume_state["run_id"],
                "result": resume_state["result"],
                "error": resume_state["error"],
                "resume_path": settings.profile.resume_path,
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
        # Re-exec sys.argv[0] directly rather than routing through
        # sys.executable: on Windows, an installed console script
        # (jobcopilot.exe) is itself a runnable launcher, not a .py file
        # python.exe can take as an argument, so it needs to be the program
        # being executed, not a value passed to the interpreter.
        os.execv(sys.argv[0], sys.argv)

    @app.post("/restart")
    def restart_server():
        # Delay the actual re-exec until after this response has had a
        # moment to reach the browser — doing it inline would kill the
        # process (and the socket) before the client ever sees a reply.
        threading.Timer(0.5, _shutdown_and_reexec).start()
        return JSONResponse({"restarting": True})

    return app
