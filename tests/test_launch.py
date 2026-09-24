import pytest

from jobcopilot.config import Settings, load_settings
from jobcopilot.launch import DashboardLaunchConfig, validate_launch_mode


def test_launch_config_builds_local_dashboard_url():
    assert DashboardLaunchConfig(host="127.0.0.1", port=8420).url == "http://127.0.0.1:8420"
    assert DashboardLaunchConfig(host="0.0.0.0", port=9000).url == "http://127.0.0.1:9000"


@pytest.mark.parametrize("mode", ["none", "browser", "webview"])
def test_validate_launch_mode_accepts_supported_modes(mode):
    assert validate_launch_mode(mode) == mode


def test_validate_launch_mode_rejects_unknown_mode():
    with pytest.raises(ValueError, match="Unsupported dashboard launch mode"):
        validate_launch_mode("installer")


def test_dashboard_launch_mode_defaults_to_existing_foreground_behavior():
    assert Settings().dashboard.launch_mode == "none"


def test_dashboard_launch_mode_is_loaded_from_yaml(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("dashboard:\n  launch_mode: browser\n", encoding="utf-8")
    assert load_settings(config).dashboard.launch_mode == "browser"
