"""Background scheduler used by `hanarr serve`. Runs search cycles and
reminder checks on the intervals set in config.yaml.
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from .config import Settings
from .db import get_or_create_profile, make_session_factory
from .llm import build_llm_client
from .models import utc_now
from .pipeline import run_search_cycle
from .reminders import deliver_reminders, get_due_reminders

logger = logging.getLogger(__name__)


def start_scheduler(settings: Settings) -> BackgroundScheduler:
    session_factory = make_session_factory(settings)
    llm = build_llm_client(settings.llm)
    scheduler = BackgroundScheduler()

    def _search_job():
        with session_factory() as session:
            profile = get_or_create_profile(session, settings)
            try:
                n = run_search_cycle(session, settings, profile, llm)
                logger.info("Search cycle complete: %d new posting(s)", n)
                profile.last_search_at = utc_now()
                profile.last_search_new_count = n
                profile.last_search_trigger = "scheduled"
                session.commit()
            except Exception:  # noqa: BLE001
                logger.exception("Search cycle failed")

    def _reminder_job():
        with session_factory() as session:
            profile = get_or_create_profile(session, settings)
            due = get_due_reminders(session, profile)
            deliver_reminders(due, settings.reminders)

    # Explicit ids so the dashboard can look these jobs up by name (to show
    # "next scheduled search"/"next reminder check") rather than relying on
    # APScheduler's auto-generated ids, which aren't predictable.
    scheduler.add_job(
        _search_job, "interval", hours=settings.schedule.search_interval_hours, id="search"
    )
    scheduler.add_job(
        _reminder_job, "interval", hours=settings.schedule.reminder_check_interval_hours, id="reminders"
    )
    scheduler.start()
    return scheduler
