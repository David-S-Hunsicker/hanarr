"""Minimal read/write dashboard: view matched jobs and reminders, update
a job's status. Deliberately server-rendered (Jinja2, a few forms) rather
than a JS framework — keeps the project approachable to contribute to and
easy to self-host with nothing but `jobcopilot serve`.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from ..config import Settings
from ..db import get_or_create_profile, make_session_factory
from ..models import ApplicationStatus, JobPosting
from ..reminders import get_due_reminders

TEMPLATES_DIR = Path(__file__).parent / "templates"


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="job-search-copilot")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    session_factory = make_session_factory(settings)

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
                "index.html",
                {
                    "request": request,
                    "jobs": jobs,
                    "reminders": reminders,
                    "statuses": [s.value for s in ApplicationStatus],
                    "current_filter": status or "",
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

    return app
