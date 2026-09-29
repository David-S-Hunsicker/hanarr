from pathlib import Path

import pytest
from pydantic import ValidationError

from hanarr import secrets_store
from hanarr.config import Preferences, ScheduleConfig, Settings, effective_preferences, load_settings, save_settings_to_yaml
from hanarr.models import Profile


def test_schedule_config_rejects_search_interval_below_one_hour():
    """"People won't want to burn out their machines" -- an accidental 0
    or negative search_interval_hours would make APScheduler fire the
    search job (real LLM/GPU load) back-to-back with no real gap."""
    with pytest.raises(ValidationError):
        ScheduleConfig(search_interval_hours=0)
    with pytest.raises(ValidationError):
        ScheduleConfig(search_interval_hours=-5)
    ScheduleConfig(search_interval_hours=1)  # the floor itself is allowed


def test_schedule_config_allows_multi_day_intervals():
    """Days are just hours * 24 under the hood -- no separate unit
    concept in the stored config, so nothing stops someone from
    configuring "every 3 days" (72) or longer."""
    settings = Settings(schedule=ScheduleConfig(search_interval_hours=72))
    assert settings.schedule.search_interval_hours == 72


def test_schedule_config_normalizes_time_of_day_to_zero_padded_hh_mm():
    schedule = ScheduleConfig(search_time_of_day="9:5")
    assert schedule.search_time_of_day == "09:05"


def test_schedule_config_rejects_an_invalid_time_of_day():
    with pytest.raises(ValidationError):
        ScheduleConfig(search_time_of_day="25:00")
    with pytest.raises(ValidationError):
        ScheduleConfig(search_time_of_day="not-a-time")


def test_schedule_config_daily_mode_defaults_to_nine_am():
    schedule = ScheduleConfig()
    assert schedule.search_schedule_mode == "interval"
    assert schedule.search_time_of_day == "09:00"


def test_missing_config_is_copied_from_template_instead_of_raising(tmp_path, monkeypatch):
    """A missing config.yaml is a first-run signal, not an error: load_settings
    must copy config.example.yaml into place and load normally rather than
    raising FileNotFoundError and forcing a separate `hanarr init` step."""
    monkeypatch.chdir(tmp_path)
    example = tmp_path / "config.example.yaml"
    example.write_text("preferences:\n  salary_floor_usd: 123000\n", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    assert not config_path.exists()

    settings = load_settings(config_path)

    assert config_path.exists()
    assert config_path.read_text(encoding="utf-8") == example.read_text(encoding="utf-8")
    assert settings.preferences.salary_floor_usd == 123000


def test_missing_config_and_missing_template_still_loads_defaults(tmp_path, monkeypatch):
    """If even config.example.yaml is unavailable (e.g. run outside a repo
    checkout), startup should still succeed with built-in defaults rather
    than crash."""
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "config.yaml"

    settings = load_settings(config_path)

    assert not config_path.exists()
    assert settings.dashboard.launch_mode == "none"


def test_load_settings_reads_anthropic_key_from_keyring_before_env(tmp_path, monkeypatch):
    """The OS credential store (written to via the Settings UI) is the
    primary source; a plain ANTHROPIC_API_KEY env var is only a fallback
    for anyone who set one up before that UI existed."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text("llm:\n  provider: anthropic\n", encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    monkeypatch.setattr(secrets_store, "get_secret", lambda name: "from-keyring")

    settings = load_settings(tmp_path / "config.yaml")

    assert settings.llm.api_key == "from-keyring"


def test_load_settings_falls_back_to_env_when_keyring_has_no_value(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text("llm:\n  provider: anthropic\n", encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    monkeypatch.setattr(secrets_store, "get_secret", lambda name: None)

    settings = load_settings(tmp_path / "config.yaml")

    assert settings.llm.api_key == "from-env"


def test_existing_config_is_left_untouched(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.example.yaml").write_text("preferences:\n  salary_floor_usd: 999\n", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text("preferences:\n  salary_floor_usd: 1\n", encoding="utf-8")

    settings = load_settings(config_path)

    assert settings.preferences.salary_floor_usd == 1


def test_save_settings_to_yaml_backs_up_the_previous_file(tmp_path):
    """config.yaml has no version history of its own -- a real incident
    (locations, salary floor, and every job-source board silently wiped by
    a stale autosave) showed this is otherwise unrecoverable. Every save
    must keep a copy of what was there before."""
    settings = Settings(data_dir=tmp_path / "data")
    config_path = tmp_path / "config.yaml"
    config_path.write_text("preferences:\n  salary_floor_usd: 111111\n", encoding="utf-8")

    save_settings_to_yaml(settings, config_path)

    backups = list((tmp_path / "data" / "backups").glob("config-*.yaml"))
    assert len(backups) == 1
    assert "111111" in backups[0].read_text(encoding="utf-8")


def test_save_settings_to_yaml_does_not_back_up_when_no_file_exists_yet(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    config_path = tmp_path / "config.yaml"  # never created

    save_settings_to_yaml(settings, config_path)

    backups_dir = tmp_path / "data" / "backups"
    assert not backups_dir.exists() or not list(backups_dir.glob("config-*.yaml"))


def test_save_settings_to_yaml_prunes_backups_beyond_the_limit(tmp_path):
    from hanarr.config import MAX_CONFIG_BACKUPS

    settings = Settings(data_dir=tmp_path / "data")
    config_path = tmp_path / "config.yaml"
    config_path.write_text("preferences: {}\n", encoding="utf-8")

    for _ in range(MAX_CONFIG_BACKUPS + 5):
        save_settings_to_yaml(settings, config_path)

    backups = list((tmp_path / "data" / "backups").glob("config-*.yaml"))
    assert len(backups) <= MAX_CONFIG_BACKUPS


def test_effective_preferences_falls_back_to_shared_settings_when_profile_has_none():
    settings = Settings()
    settings.preferences.target_titles = ["Backend Engineer"]
    profile = Profile(name="Someone")  # preferences_json defaults to ""

    prefs = effective_preferences(profile, settings)

    assert prefs.target_titles == ["Backend Engineer"]
    assert prefs is settings.preferences  # a live fallback, not a copy


def test_effective_preferences_uses_the_profiles_own_saved_preferences_once_set():
    settings = Settings()
    settings.preferences.target_titles = ["Backend Engineer"]
    own_prefs = Preferences(target_titles=["Data Scientist"], locations=["Remote"])
    profile = Profile(name="Someone else", preferences_json=own_prefs.model_dump_json())

    prefs = effective_preferences(profile, settings)

    assert prefs.target_titles == ["Data Scientist"]
    assert prefs.locations == ["Remote"]


def test_effective_preferences_does_not_change_the_shared_settings_object():
    """Two profiles, one with its own preferences and one without, must
    never see each other's values through a shared mutable object."""
    settings = Settings()
    settings.preferences.target_titles = ["Shared Default Title"]
    forked = Profile(name="Forked", preferences_json=Preferences(target_titles=["Custom"]).model_dump_json())
    unforked = Profile(name="Unforked")

    assert effective_preferences(forked, settings).target_titles == ["Custom"]
    assert effective_preferences(unforked, settings).target_titles == ["Shared Default Title"]
