import hanarr.scheduler as scheduler_mod
from hanarr.config import Settings
from hanarr.db import make_session_factory, get_or_create_profile
from hanarr.models import Profile


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
