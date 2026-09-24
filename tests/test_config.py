from pathlib import Path

from jobcopilot.config import load_settings


def test_missing_config_is_copied_from_template_instead_of_raising(tmp_path, monkeypatch):
    """A missing config.yaml is a first-run signal, not an error: load_settings
    must copy config.example.yaml into place and load normally rather than
    raising FileNotFoundError and forcing a separate `jobcopilot init` step."""
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


def test_existing_config_is_left_untouched(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.example.yaml").write_text("preferences:\n  salary_floor_usd: 999\n", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text("preferences:\n  salary_floor_usd: 1\n", encoding="utf-8")

    settings = load_settings(config_path)

    assert settings.preferences.salary_floor_usd == 1
