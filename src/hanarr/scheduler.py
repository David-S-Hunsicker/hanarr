"""Background scheduler used by `hanarr serve`. Runs search cycles,
reminder checks, and (if enabled) update checks on the intervals set in
config.yaml.
"""
from __future__ import annotations

import json
import logging
import time

import tzlocal
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from . import self_update
from .config import Settings, effective_preferences
from .db import get_or_create_profile, list_profiles, make_session_factory
from .llm import build_llm_client
from .models import Profile, utc_now
from .pipeline import run_search_cycle
from .reminders import deliver_reminders, get_due_reminders
from .search_state import new_rescore_state, new_search_state, on_progress as search_on_progress, reset_for_run
from .update_service import UpdateCheckError, check_for_update
from .update_state import clear as clear_update_state, mark_checked, new_update_state, reset_for_update

logger = logging.getLogger(__name__)

# How often the apply-tick job checks whether a staged, non-cancelled
# update's delay has elapsed and no search is running. Cheap (a few dict
# reads when nothing is pending), so a short interval keeps the countdown
# banner's promised time accurate without meaningfully polling anything.
UPDATE_APPLY_TICK_SECONDS = 15


def start_scheduler(
    settings: Settings, search_state: dict | None = None, update_state: dict | None = None,
    rescore_state: dict | None = None,
) -> BackgroundScheduler:
    """`search_state`/`update_state`/`rescore_state` are the same shared
    dicts passed to create_app() -- pass the same objects to both so a
    scheduled search or a found/staged update shows up on the dashboard
    identically to something triggered manually, and so a scheduled search
    refuses to start while a dashboard-triggered rescore is using the LLM
    (and vice versa -- see create_app's trigger_rescore route). Before
    search_state existed, the scheduled search job ran with no progress
    callback at all, so an automatic background search (real GPU/CPU load,
    potentially for minutes) happened with nothing on the dashboard to show
    it was happening. Omit any of these (e.g. in a test that only exercises
    part of the scheduler) and a fresh, unshared one is created."""
    if search_state is None:
        search_state = new_search_state()
    if update_state is None:
        update_state = new_update_state()
    if rescore_state is None:
        rescore_state = new_rescore_state()
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
        if rescore_state["running"]:
            # A dashboard-triggered "Rescore all jobs" is using the LLM --
            # starting a search too would have both write to JobPosting
            # rows from separate sessions against the same SQLite file.
            logger.info("Skipping scheduled search -- a rescore is already running.")
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

        try:
            for profile_id in profile_ids:
                reset_for_run(search_state, search_state["run_id"] + 1, "scheduled")
                with session_factory() as session:
                    profile = session.get(Profile, profile_id)
                    if profile is None:
                        continue
                    # Profile-based board filtering (see company_categories.py)
                    # can make different profiles query a different number of
                    # sources, so this has to be computed per-profile rather
                    # than once for the whole run.
                    resume_summary = json.loads(profile.resume_summary_json or "{}")
                    search_state["sources_total"] = len(build_enabled_connectors(
                        settings.sources, resume_summary=resume_summary,
                        preferences=effective_preferences(profile, settings),
                    ))
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

    def _update_check_job():
        """Checks release metadata only (same call the manual "Check now"
        button makes) -- auto_update=False stops here, matching today's
        check-only behavior exactly. auto_update=True additionally
        downloads and verifies the installer and stages a delayed,
        cancellable auto-apply via _update_apply_tick_job below."""
        if not settings.updates.enabled:
            return
        try:
            result = check_for_update(settings)
        except UpdateCheckError as exc:
            logger.warning("Scheduled update check failed: %s", exc)
            return
        finally:
            mark_checked(update_state)

        if result.get("status") != "update_available" or not settings.updates.auto_update:
            return
        if update_state["available"] and update_state["version"] == result["release"]["version"]:
            return  # already staged this exact version -- nothing new to do

        release_dict = result["release"]
        release = self_update.release_from_check_result(release_dict)
        asset = self_update.find_windows_installer_asset(release)
        if asset is None:
            logger.warning("Update %s has no Windows installer asset; skipping.", release_dict["version"])
            return

        destination = settings.data_dir / "updates" / asset.name
        try:
            installer_path = self_update.download_and_verify_installer(asset, destination)
        except self_update.SelfUpdateError as exc:
            logger.warning("Could not download/verify update %s: %s", release_dict["version"], exc)
            update_state["error"] = str(exc)[:300]
            return

        reset_for_update(
            update_state,
            version=release_dict["version"],
            notes_url=release_dict.get("release_notes_url", ""),
            installer_path=str(installer_path),
            apply_at=time.time() + settings.updates.apply_delay_seconds,
        )
        logger.info(
            "Update %s downloaded and verified; applying in %ds unless cancelled.",
            release_dict["version"], settings.updates.apply_delay_seconds,
        )

    def _update_apply_tick_job():
        if not update_state["available"] or update_state["cancelled"] or update_state["applying"]:
            return
        if update_state["apply_at"] is None or time.time() < update_state["apply_at"]:
            return
        if search_state["search_running"]:
            return  # never interrupt an in-progress search -- try again next tick
        if not self_update.is_packaged_build():
            # Nothing to actually replace in a source checkout -- leave the
            # banner/state as-is rather than silently pretending to apply.
            return
        update_state["applying"] = True
        try:
            self_update.apply_update(update_state["installer_path"])
            # Setup's own CloseApplications/RestartApplications (see
            # installer/hanarr.iss) closes and relaunches this app as part
            # of the install -- this process doesn't need to (and
            # shouldn't try to) exit itself here.
        except self_update.SelfUpdateError as exc:
            logger.exception("Failed to apply staged update %s", update_state["version"])
            update_state["error"] = str(exc)[:300]
            update_state["applying"] = False

    if settings.schedule.search_schedule_mode == "daily":
        hour, minute = (int(part) for part in settings.schedule.search_time_of_day.split(":"))
        # Named explicitly rather than left to CronTrigger's own default --
        # "daily at 9am" should mean 9am on this machine, not UTC, and
        # naming the zone here makes that a guarantee instead of an
        # implicit default that could silently change with the environment.
        search_trigger = CronTrigger(hour=hour, minute=minute, timezone=tzlocal.get_localzone())
    else:
        search_trigger = IntervalTrigger(hours=settings.schedule.search_interval_hours)

    # Explicit ids so the dashboard can look these jobs up by name (to show
    # "next scheduled search"/"next reminder check") rather than relying on
    # APScheduler's auto-generated ids, which aren't predictable.
    scheduler.add_job(_search_job, search_trigger, id="search")
    scheduler.add_job(
        _reminder_job, "interval", hours=settings.schedule.reminder_check_interval_hours, id="reminders"
    )
    if settings.updates.enabled:
        scheduler.add_job(
            _update_check_job, "interval", hours=settings.updates.check_interval_hours, id="update_check",
        )
        scheduler.add_job(
            _update_apply_tick_job, "interval", seconds=UPDATE_APPLY_TICK_SECONDS, id="update_apply_tick",
        )
    scheduler.start()
    return scheduler
