"""Minimal read/write dashboard: view matched jobs and reminders, update
a job's status, and trigger a search or reminder check on demand.
Deliberately server-rendered (Jinja2, a few forms) rather than a JS
framework — keeps the project approachable to contribute to and easy to
self-host with nothing but `jobcopilot serve`.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..config import DEFAULT_CONFIG_PATH, Settings
from ..db import get_or_create_profile, make_session_factory
from ..llm import build_llm_client
from ..models import ApplicationStatus, JobPosting
from ..pipeline import run_search_cycle
from ..reminders import deliver_reminders, get_due_reminders, mark_completed
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


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="job-search-copilot")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.cache = None
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
            _log_event({"kind": "info", "text": f"{event['source']}: {event['count']} posting(s) fetched"})
        elif kind == "source_error":
            _log_event({"kind": "error", "text": f"{event['source']}: fetch failed, skipping"})
        elif kind == "scoring":
            _log_event({"kind": "scoring", "text": f"Scoring: {event['title']} at {event['company']}"})
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

    @app.get("/")
    def index(request: Request, status: str | None = None):
        with session_factory() as session:
            profile = get_or_create_profile(session, settings)
            query = session.query(JobPosting).filter(JobPosting.profile_id == profile.id)
            if status:
                query = query.filter(JobPosting.status == ApplicationStatus(status))
            jobs = query.order_by(JobPosting.fit_score.desc()).all()
            reminders = get_due_reminders(session, profile)
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

    @app.post("/config/preferences")
    async def save_preferences(request: Request):
        return await _handle_config_post(request, "preferences", apply_preferences_form)

    @app.post("/config/app")
    async def save_app_config(request: Request):
        return await _handle_config_post(request, "app", apply_app_config_form)

    @app.post("/config/schedule")
    async def save_schedule_reminders(request: Request):
        return await _handle_config_post(request, "schedule", apply_schedule_reminders_form)

    return app
