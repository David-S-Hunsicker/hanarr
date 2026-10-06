import time

import tzlocal
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import hanarr.scheduler as scheduler_mod
import hanarr.self_update as self_update_mod
from hanarr.config import Settings
from hanarr.db import make_session_factory, get_or_create_profile
from hanarr.models import Profile
from hanarr.search_state import new_rescore_state, new_search_state
from hanarr.update_state import new_update_state, reset_for_update


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


def test_scheduled_search_skips_while_a_rescore_is_running(tmp_path, monkeypatch):
    """A dashboard-triggered "Rescore all jobs" is also a live, exclusive
    LLM user -- a scheduled search starting underneath it would hit the
    LLM concurrently and write JobPosting rows from a separate session
    against the same SQLite file."""
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

    rescore_state = new_rescore_state()
    rescore_state["running"] = True  # simulates a dashboard-triggered rescore in progress
    scheduler = scheduler_mod.start_scheduler(settings, rescore_state=rescore_state)
    try:
        search_job = scheduler.get_job("search").func
        search_job()
    finally:
        scheduler.shutdown(wait=False)

    assert call_count == 0, "a scheduled search must not start while a rescore is running"


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


def _release_dict(version="0.1.3", exe_name="Hanarr-Setup-0.1.3.exe"):
    return {
        "version": version,
        "release_notes_url": "https://example.test/notes",
        "notes": "",
        "assets": [
            {"name": exe_name, "url": "https://example.test/installer.exe", "sha256": "a" * 64, "platform": ""},
            {"name": exe_name.replace(".exe", ".sha256"), "url": "https://example.test/checksum", "sha256": "", "platform": ""},
        ],
    }


def test_update_check_job_does_nothing_when_checks_are_disabled(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    settings.updates.enabled = False

    def fail_if_called(*a, **k):
        raise AssertionError("check_for_update must not be called when updates are disabled")

    monkeypatch.setattr(scheduler_mod, "check_for_update", fail_if_called)

    scheduler = scheduler_mod.start_scheduler(settings)
    try:
        assert scheduler.get_job("update_check") is None
        assert scheduler.get_job("update_apply_tick") is None
    finally:
        scheduler.shutdown(wait=False)


def test_update_check_job_does_nothing_when_already_up_to_date(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    settings.updates.auto_update = True
    monkeypatch.setattr(
        scheduler_mod, "check_for_update",
        lambda settings: {"status": "up_to_date", "current_version": "9.9.9"},
    )

    update_state = new_update_state()
    scheduler = scheduler_mod.start_scheduler(settings, update_state=update_state)
    try:
        scheduler.get_job("update_check").func()
    finally:
        scheduler.shutdown(wait=False)

    assert update_state["available"] is False
    assert update_state["last_checked_at"] is not None


def test_update_check_job_skips_downloading_when_auto_update_is_off(tmp_path, monkeypatch):
    """auto_update=False must behave exactly like today's manual
    "Check now" -- metadata only, never a download."""
    settings = _settings(tmp_path)
    settings.updates.auto_update = False
    monkeypatch.setattr(
        scheduler_mod, "check_for_update",
        lambda settings: {"status": "update_available", "current_version": "0.1.0", "release": _release_dict()},
    )

    def fail_if_called(*a, **k):
        raise AssertionError("must not download when auto_update is off")

    monkeypatch.setattr(self_update_mod, "download_and_verify_installer", fail_if_called)

    update_state = new_update_state()
    scheduler = scheduler_mod.start_scheduler(settings, update_state=update_state)
    try:
        scheduler.get_job("update_check").func()
    finally:
        scheduler.shutdown(wait=False)

    assert update_state["available"] is False


def test_update_check_job_downloads_and_stages_when_auto_update_is_on(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    settings.updates.auto_update = True
    settings.updates.apply_delay_seconds = 120
    monkeypatch.setattr(
        scheduler_mod, "check_for_update",
        lambda settings: {"status": "update_available", "current_version": "0.1.0", "release": _release_dict()},
    )

    staged_path = tmp_path / "Hanarr-Setup-0.1.3.exe"
    monkeypatch.setattr(self_update_mod, "download_and_verify_installer", lambda asset, dest, **k: staged_path)

    update_state = new_update_state()
    before = time.time()
    scheduler = scheduler_mod.start_scheduler(settings, update_state=update_state)
    try:
        scheduler.get_job("update_check").func()
    finally:
        scheduler.shutdown(wait=False)

    assert update_state["available"] is True
    assert update_state["version"] == "0.1.3"
    assert update_state["installer_path"] == str(staged_path)
    assert update_state["apply_at"] >= before + 120
    assert update_state["cancelled"] is False


def test_update_check_job_records_an_error_when_download_fails(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    settings.updates.auto_update = True
    monkeypatch.setattr(
        scheduler_mod, "check_for_update",
        lambda settings: {"status": "update_available", "current_version": "0.1.0", "release": _release_dict()},
    )

    def fail(*a, **k):
        raise self_update_mod.SelfUpdateError("checksum mismatch")

    monkeypatch.setattr(self_update_mod, "download_and_verify_installer", fail)

    update_state = new_update_state()
    scheduler = scheduler_mod.start_scheduler(settings, update_state=update_state)
    try:
        scheduler.get_job("update_check").func()  # must not raise
    finally:
        scheduler.shutdown(wait=False)

    assert update_state["available"] is False
    assert "checksum mismatch" in update_state["error"]


def test_update_apply_tick_does_nothing_when_no_update_is_staged(tmp_path):
    settings = _settings(tmp_path)
    update_state = new_update_state()
    scheduler = scheduler_mod.start_scheduler(settings, update_state=update_state)
    try:
        scheduler.get_job("update_apply_tick").func()  # must not raise
    finally:
        scheduler.shutdown(wait=False)
    assert update_state["applying"] is False


def test_update_apply_tick_waits_until_apply_at_has_passed(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr(self_update_mod, "is_packaged_build", lambda: True)

    def fail(path):
        raise AssertionError("too early")

    monkeypatch.setattr(self_update_mod, "apply_update", fail)

    update_state = new_update_state()
    reset_for_update(update_state, version="0.1.3", notes_url="x", installer_path="x", apply_at=time.time() + 3600)

    scheduler = scheduler_mod.start_scheduler(settings, update_state=update_state)
    try:
        scheduler.get_job("update_apply_tick").func()
    finally:
        scheduler.shutdown(wait=False)

    assert update_state["applying"] is False


def test_update_apply_tick_never_applies_while_a_search_is_running(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr(self_update_mod, "is_packaged_build", lambda: True)

    def fail(path):
        raise AssertionError("must not apply during a search")

    monkeypatch.setattr(self_update_mod, "apply_update", fail)

    search_state = new_search_state()
    search_state["search_running"] = True
    update_state = new_update_state()
    reset_for_update(update_state, version="0.1.3", notes_url="x", installer_path="x", apply_at=time.time() - 1)

    scheduler = scheduler_mod.start_scheduler(settings, search_state=search_state, update_state=update_state)
    try:
        scheduler.get_job("update_apply_tick").func()
    finally:
        scheduler.shutdown(wait=False)

    assert update_state["applying"] is False


def test_update_apply_tick_skips_a_cancelled_update(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr(self_update_mod, "is_packaged_build", lambda: True)

    def fail(path):
        raise AssertionError("must not apply a cancelled update")

    monkeypatch.setattr(self_update_mod, "apply_update", fail)

    update_state = new_update_state()
    reset_for_update(update_state, version="0.1.3", notes_url="x", installer_path="x", apply_at=time.time() - 1)
    update_state["cancelled"] = True

    scheduler = scheduler_mod.start_scheduler(settings, update_state=update_state)
    try:
        scheduler.get_job("update_apply_tick").func()
    finally:
        scheduler.shutdown(wait=False)

    assert update_state["applying"] is False


def test_update_apply_tick_skips_in_a_source_checkout():
    """is_packaged_build() is genuinely False for the test process itself
    -- confirms the real function, not just a mocked stand-in, correctly
    refuses to apply outside a packaged build."""
    assert self_update_mod.is_packaged_build() is False


def test_update_apply_tick_applies_when_due_idle_and_not_cancelled(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr(self_update_mod, "is_packaged_build", lambda: True)
    applied = []
    monkeypatch.setattr(self_update_mod, "apply_update", lambda path: applied.append(path))

    update_state = new_update_state()
    reset_for_update(
        update_state, version="0.1.3", notes_url="x",
        installer_path="/tmp/Hanarr-Setup-0.1.3.exe", apply_at=time.time() - 1,
    )

    scheduler = scheduler_mod.start_scheduler(settings, update_state=update_state)
    try:
        scheduler.get_job("update_apply_tick").func()
    finally:
        scheduler.shutdown(wait=False)

    assert applied == ["/tmp/Hanarr-Setup-0.1.3.exe"]
    assert update_state["applying"] is True


def test_update_apply_tick_records_an_error_if_apply_fails(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr(self_update_mod, "is_packaged_build", lambda: True)

    def fail(path):
        raise self_update_mod.SelfUpdateError("installer not found")

    monkeypatch.setattr(self_update_mod, "apply_update", fail)

    update_state = new_update_state()
    reset_for_update(update_state, version="0.1.3", notes_url="x", installer_path="x", apply_at=time.time() - 1)

    scheduler = scheduler_mod.start_scheduler(settings, update_state=update_state)
    try:
        scheduler.get_job("update_apply_tick").func()  # must not raise
    finally:
        scheduler.shutdown(wait=False)

    assert update_state["applying"] is False
    assert "installer not found" in update_state["error"]
