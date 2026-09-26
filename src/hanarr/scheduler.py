"""Background scheduler used by `hanarr serve`. Runs search cycles and
reminder checks on the intervals set in config.yaml.
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from .config import Settings
from .db import get_or_create_profile, list_profiles, make_session_factory
from .llm import build_llm_client
from .models import Profile, utc_now
from .pipeline import run_search_cycle
from .reminders import deliver_reminders, get_due_reminders
from .search_state import new_search_state, on_progress as search_on_progress, reset_for_run

logger = logging.getLogger(__name__)


def start_scheduler(settings: Settings, search_state: dict | None = None) -> BackgroundScheduler:
    """`search_state` is the same shared dict passed to create_app() --
    pass the same object to both so a scheduled search shows up on the
    dashboard identically to a manual one. Before this, the scheduled job
    ran with no progress callback at all, so an automatic background
    search (real GPU/CPU load, potentially for minutes) happened with
    nothing on the dashboard to show it was happening. Omit it (e.g. in a
    test that only exercises the scheduler) and one is created fresh,
    unshared."""
    if search_state is None:
        search_state = new_search_state()
    session_factory = make_session_factory(settings)
    llm = build_llm_client(settings.llm)
    scheduler = BackgroundScheduler()

    def _search_job():
        if search_state["search_running"]:
            # A manual "Run search now" (or a previous scheduled run that's
            # somehow still going) is already in progress -- never run two
            # searches at once, since they'd share and corrupt the same
            # progress counters, and it doubles the load on the same LLM.
            logger.info("Skipping scheduled search -- a search is already running.")
            return

        # Search preferences (titles, locations, connectors) are shared
        # across every local profile, but each profile's own resume/match
        # history is independent -- so a scheduled run does a full cycle
        # per profile rather than just the first one, once multiple
        # profiles exist.
        with session_factory() as session:
            profiles = list_profiles(session) or [get_or_create_profile(session, settings)]
            profile_ids = [p.id for p in profiles]

        from .connectors import build_enabled_connectors

        sources_total = len(build_enabled_connectors(settings.sources))
        try:
            for profile_id in profile_ids:
                reset_for_run(search_state, search_state["run_id"] + 1, "scheduled")
                search_state["sources_total"] = sources_total
                with session_factory() as session:
                    profile = session.get(Profile, profile_id)
                    if profile is None:
                        continue
                    try:
                        n = run_search_cycle(
                            session, settings, profile, llm,
                            on_progress=lambda e: search_on_progress(search_state, e),
                        )
                        logger.info(
                            "Search cycle complete for profile %d (%s): %d new posting(s)",
                            profile.id, profile.name, n,
                        )
                        profile.last_search_at = utc_now()
                        profile.last_search_new_count = n
                        profile.last_search_trigger = "scheduled"
                        session.commit()
                        search_state["last_search_result"] = f"{n} new posting(s) matched and stored (scheduled)."
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("Search cycle failed for profile %d (%s)", profile.id, profile.name)
                        message = f"Scheduled search failed — {exc}"
                        search_state["last_search_result"] = message[:300]
        finally:
            search_state["search_running"] = False

    def _reminder_job():
        with session_factory() as session:
            for profile in list_profiles(session) or [get_or_create_profile(session, settings)]:
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
