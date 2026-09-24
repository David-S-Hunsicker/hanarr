import socket
import threading
import time

import pytest

from hanarr.config import Settings, load_settings
from hanarr.launch import DashboardLaunchConfig, _wait_for_port_available, validate_launch_mode


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


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_wait_for_port_available_returns_immediately_when_free():
    port = _free_port()
    started = time.monotonic()

    _wait_for_port_available("127.0.0.1", port, timeout_seconds=5.0)

    assert time.monotonic() - started < 1.0


def test_wait_for_port_available_waits_out_a_briefly_busy_port():
    """Regression test for a real reported hang: a restarted server's
    replacement process can start before the old process has released the
    port yet. This must absorb that race rather than let the real bind
    attempt fail outright."""
    port = _free_port()
    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupied.bind(("127.0.0.1", port))
    occupied.listen(1)

    def release_after_a_moment():
        time.sleep(0.3)
        occupied.close()

    threading.Thread(target=release_after_a_moment, daemon=True).start()
    started = time.monotonic()

    _wait_for_port_available("127.0.0.1", port, timeout_seconds=5.0)
    elapsed = time.monotonic() - started

    assert 0.3 <= elapsed < 5.0


def test_wait_for_port_available_gives_up_without_raising():
    """Best-effort only: it must return (not raise) once the timeout
    elapses, leaving the real bind attempt to raise its own clear error
    rather than this helper hanging or crashing first."""
    port = _free_port()
    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupied.bind(("127.0.0.1", port))
    occupied.listen(1)
    try:
        started = time.monotonic()
        _wait_for_port_available("127.0.0.1", port, timeout_seconds=0.3)
        assert time.monotonic() - started >= 0.3
    finally:
        occupied.close()
