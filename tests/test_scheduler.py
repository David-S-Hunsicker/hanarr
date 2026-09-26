import tzlocal
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import hanarr.scheduler as scheduler_mod
from hanarr.config import Settings
from hanarr.db import make_session_factory, get_or_create_profile
from hanarr.models import Profile
from hanarr.search_state import new_search_state


def _settings(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    settings.llm.provider = "none"
    return settings


def test_search_job_runs_a_cycle_for_every_profile(tmp_path, monkeypatch):
    """Regression test: search preferences are shared across profiles, but
    each profile's own resume/match history is independent -- a scheduled
    search must not silently only ever run for the first profile once a
    second one exists."""
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        default_profile = get_or_create_profile(session, settings)
        second_profile = Profile(name="Jordan")
        session.add(second_profile)
        session.commit()
        default_id, second_id = default_profile.id, second_profile.id

    seen_profile_ids = []

    def fake_run_search_cycle(session, settings, profile, llm, **kwargs):
        seen_profile_ids.append(profile.id)
        return 3

    monkeypatch.setattr(scheduler_mod, "run_search_cycle", fake_run_search_cycle)
    monkeypatch.setattr(scheduler_mod, "build_llm_client", lambda cfg: object())

    scheduler = scheduler_mod.start_scheduler(settings)
    try:
        search_job = scheduler.get_job("search").func
        search_job()
    finally:
        scheduler.shutdown(wait=False)

    assert sorted(seen_profile_ids) == sorted([default_id, second_id])

    with factory() as session:
        default_profile = session.get(Profile, default_id)
        second_profile = session.get(Profile, second_id)
        assert default_profile.last_search_new_count == 3
        assert second_profile.last_search_new_count == 3
        assert default_profile.last_search_trigger == "scheduled"
        assert second_profile.last_search_trigger == "scheduled"


def test_search_job_continues_to_other_profiles_after_one_fails(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        default_profile = get_or_create_profile(session, settings)
        second_profile = Profile(name="Jordan")
        session.add(second_profile)
        session.commit()
        default_id, second_id = default_profile.id, second_profile.id

    def flaky_run_search_cycle(session, settings, profile, llm, **kwargs):
        if profile.id == default_id:
            raise RuntimeError("boom")
        return 1

    monkeypatch.setattr(scheduler_mod, "run_search_cycle", flaky_run_search_cycle)
    monkeypatch.setattr(scheduler_mod, "build_llm_client", lambda cfg: object())

    scheduler = scheduler_mod.start_scheduler(settings)
    try:
        search_job = scheduler.get_job("search").func
        search_job()
    finally:
        scheduler.shutdown(wait=False)

    with factory() as session:
        default_profile = session.get(Profile, default_id)
        second_profile = session.get(Profile, second_id)
        assert default_profile.last_search_at is None
        assert second_profile.last_search_new_count == 1


def test_scheduled_search_updates_the_shared_search_state(tmp_path, monkeypatch):
    """A scheduled search used to run with no progress callback at all --
    it happened with nothing visible on the dashboard. Passing the same
    search_state object to start_scheduler() and create_app() (as cli.py's
    `serve` command does) must make a scheduled run show up identically
    to a manual one."""
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        get_or_create_profile(session, settings)

    def fake_run_search_cycle(session, settings, profile, llm, on_progress=None, **kwargs):
        if on_progress:
            on_progress({"event": "source_start", "source": "greenhouse"})
            on_progress({"event": "scoring", "title": "Engineer", "company": "Acme"})
            on_progress({"event": "considered"})
        return 1

    monkeypatch.setattr(scheduler_mod, "run_search_cycle", fake_run_search_cycle)
    monkeypatch.setattr(scheduler_mod, "build_llm_client", lambda cfg: object())

    search_state = new_search_state()
    scheduler = scheduler_mod.start_scheduler(settings, search_state=search_state)
    try:
        search_job = scheduler.get_job("search").func
        search_job()
    finally:
        scheduler.shutdown(wait=False)

    assert search_state["trigger"] == "scheduled"
    assert search_state["scoring_count"] == 1
    assert search_state["considered_done"] == 1
    assert search_state["search_running"] is False


def test_scheduled_search_skips_when_one_is_already_running(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    factory = make_session_factory(settings)
    with factory() as session:
        get_or_create_profile(session, settings)

    call_count = 0

    def fake_run_search_cycle(session, settings, profile, llm, **kwargs):
        nonlocal call_count
        call_count += 1
        return 0

    monkeypatch.setattr(scheduler_mod, "run_search_cycle", fake_run_search_cycle)
    monkeypatch.setattr(scheduler_mod, "build_llm_client", lambda cfg: object())

    search_state = new_search_state()
    search_state["search_running"] = True  # simulates a manual search already in progress
    scheduler = scheduler_mod.start_scheduler(settings, search_state=search_state)
    try:
        search_job = scheduler.get_job("search").func
        search_job()
    finally:
        scheduler.shutdown(wait=False)

    assert call_count == 0, "a scheduled search must not start while another search is running"


def test_search_job_uses_an_interval_trigger_by_default(tmp_path):
    settings = _settings(tmp_path)
    scheduler = scheduler_mod.start_scheduler(settings)
    try:
        assert isinstance(scheduler.get_job("search").trigger, IntervalTrigger)
    finally:
        scheduler.shutdown(wait=False)


def test_daily_mode_uses_a_cron_trigger_pinned_to_local_time(tmp_path):
    """Regression test: "daily at 9am" must mean 9am on this machine, not
    UTC or whatever timezone the scheduler happens to default to -- the
    trigger's timezone is set explicitly from tzlocal rather than left
    implicit."""
    settings = _settings(tmp_path)
    settings.schedule.search_schedule_mode = "daily"
    settings.schedule.search_time_of_day = "14:30"

    scheduler = scheduler_mod.start_scheduler(settings)
    try:
        trigger = scheduler.get_job("search").trigger
        assert isinstance(trigger, CronTrigger)
        assert str(trigger.timezone) == str(tzlocal.get_localzone())
        fields = {f.name: str(f) for f in trigger.fields}
        assert fields["hour"] == "14"
        assert fields["minute"] == "30"
    finally:
        scheduler.shutdown(wait=False)
