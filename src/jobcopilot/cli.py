from __future__ import annotations

import datetime as dt
import json
import logging
import shutil
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from .config import DEFAULT_CONFIG_PATH, load_settings
from .db import get_or_create_profile, make_session_factory
from .llm import build_llm_client
from .models import ApplicationStatus, JobPosting
from .pipeline import run_search_cycle
from .reminders import (
    deliver_reminders,
    get_due_reminders,
    mark_completed,
    schedule_follow_up,
    schedule_interview_prep,
)
from .resume import extract_profile_summary, load_resume_text

console = Console()


@click.group()
@click.option("--config", "config_path", default=str(DEFAULT_CONFIG_PATH), help="Path to config.yaml")
@click.pass_context
def cli(ctx: click.Context, config_path: str):
    """job-search-copilot: a self-hosted job search agent."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path


@cli.command()
@click.pass_context
def init(ctx: click.Context):
    """Create config.yaml from the template and parse your resume."""
    config_path = Path(ctx.obj["config_path"])
    if config_path.exists():
        console.print(f"[yellow]{config_path} already exists — skipping template copy.[/yellow]")
    else:
        example = Path("config.example.yaml")
        if not example.exists():
            raise click.ClickException("config.example.yaml not found — run this from the repo root.")
        shutil.copy(example, config_path)
        console.print(f"[green]Created {config_path}[/green] — edit it with your preferences, then re-run `init`.")

    settings = load_settings(config_path)
    resume_path = Path(settings.profile.resume_path)
    if not resume_path.exists():
        console.print(
            f"[yellow]No resume found at {resume_path}.[/yellow] Add one (txt/md/pdf), "
            f"update profile.resume_path in config.yaml if needed, then re-run `jobcopilot init`."
        )
        return

    console.print("Parsing resume...")
    resume_text = load_resume_text(resume_path)
    llm = build_llm_client(settings.llm)
    summary = extract_profile_summary(resume_text, llm)

    session_factory = make_session_factory(settings)
    with session_factory() as session:
        profile = get_or_create_profile(session, settings)
        profile.resume_text = resume_text
        profile.resume_summary_json = json.dumps(summary)
        session.commit()

    console.print("[green]Resume parsed and saved.[/green]")
    if summary.get("_extraction_error"):
        console.print(
            f"[yellow]Note: structured extraction failed ({summary['_extraction_error']}); "
            f"falling back to raw-text keyword matching. Check your LLM setup (Ollama running? "
            f"model pulled?).[/yellow]"
        )
    else:
        console.print(f"Detected titles: {summary.get('titles')}")
        console.print(f"Detected skills: {summary.get('skills')}")


@cli.command()
@click.pass_context
def search(ctx: click.Context):
    """Run one search cycle now (fetch, filter, score, store)."""
    settings = load_settings(ctx.obj["config_path"])
    session_factory = make_session_factory(settings)
    llm = build_llm_client(settings.llm)
    with session_factory() as session:
        profile = get_or_create_profile(session, settings)
        n = run_search_cycle(session, settings, profile, llm)
    console.print(f"[green]Search complete.[/green] {n} new posting(s) matched and stored.")


@cli.command(name="list")
@click.option("--status", "status_filter", default=None, help="Filter by status (new, applied, ...)")
@click.option("--limit", default=25, help="Max rows to show")
@click.pass_context
def list_jobs(ctx: click.Context, status_filter: str | None, limit: int):
    """List matched job postings."""
    settings = load_settings(ctx.obj["config_path"])
    session_factory = make_session_factory(settings)
    with session_factory() as session:
        profile = get_or_create_profile(session, settings)
        query = session.query(JobPosting).filter(JobPosting.profile_id == profile.id)
        if status_filter:
            query = query.filter(JobPosting.status == ApplicationStatus(status_filter))
        jobs = query.order_by(JobPosting.fit_score.desc()).limit(limit).all()

    table = Table(title="Matched jobs")
    table.add_column("ID")
    table.add_column("Score")
    table.add_column("Title")
    table.add_column("Company")
    table.add_column("Location")
    table.add_column("Status")
    for j in jobs:
        table.add_row(str(j.id), f"{j.fit_score:.0f}" if j.fit_score is not None else "-", j.title, j.company, j.location, j.status.value)
    console.print(table)


@cli.command(name="status")
@click.argument("job_id", type=int)
@click.argument("new_status", type=click.Choice([s.value for s in ApplicationStatus]))
@click.option("--interview-at", default=None, help="ISO datetime of an upcoming interview (for status=interviewing)")
@click.pass_context
def set_status(ctx: click.Context, job_id: int, new_status: str, interview_at: str | None):
    """Update a job posting's status. Marking 'applied' schedules a follow-up
    reminder; marking 'interviewing' with --interview-at schedules a prep reminder."""
    settings = load_settings(ctx.obj["config_path"])
    session_factory = make_session_factory(settings)
    with session_factory() as session:
        profile = get_or_create_profile(session, settings)
        job = session.get(JobPosting, job_id)
        if job is None or job.profile_id != profile.id:
            raise click.ClickException(f"No job with id {job_id}")

        job.status = ApplicationStatus(new_status)
        job.status_changed_at = dt.datetime.utcnow()
        session.commit()

        if job.status == ApplicationStatus.APPLIED:
            schedule_follow_up(session, profile, job, settings.reminders)
            console.print(f"[green]Marked applied.[/green] Follow-up reminder set for {settings.reminders.follow_up_after_days} day(s) out.")
        elif job.status == ApplicationStatus.INTERVIEWING and interview_at:
            when = dt.datetime.fromisoformat(interview_at)
            schedule_interview_prep(session, profile, job, when)
            console.print(f"[green]Marked interviewing.[/green] Prep reminder set for the day before.")
        else:
            console.print(f"[green]Status updated to {new_status}.[/green]")


@cli.command()
@click.pass_context
def remind(ctx: click.Context):
    """Check for and deliver any due reminders now."""
    settings = load_settings(ctx.obj["config_path"])
    session_factory = make_session_factory(settings)
    with session_factory() as session:
        profile = get_or_create_profile(session, settings)
        due = get_due_reminders(session, profile)
        if not due:
            console.print("No reminders due.")
            return
        deliver_reminders(due, settings.reminders)
        for r in due:
            console.print(f"- {r.message} (due {r.due_at.isoformat()})")
        mark_completed(session, due)


@cli.command()
@click.pass_context
def serve(ctx: click.Context):
    """Run the scheduler and dashboard together (long-running)."""
    settings = load_settings(ctx.obj["config_path"])
    from .scheduler import start_scheduler

    start_scheduler(settings)

    import uvicorn

    from .dashboard.app import create_app

    app = create_app(settings)
    console.print(f"[green]Dashboard running at http://{settings.dashboard.host}:{settings.dashboard.port}[/green]")
    uvicorn.run(app, host=settings.dashboard.host, port=settings.dashboard.port, log_level="warning")


if __name__ == "__main__":
    cli()
