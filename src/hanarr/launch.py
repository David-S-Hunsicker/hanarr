"""Launch the local dashboard in a browser or an optional desktop webview.

This module deliberately owns only the launch contract. Packaging, installer
registration, runtime bundling, and Ollama setup remain separate milestones.
"""
from __future__ import annotations

import socket
import threading
import time
import webbrowser
from dataclasses import dataclass
from typing import Any, Literal, cast

import uvicorn

LaunchMode = Literal["none", "browser", "webview"]


def _wait_for_port_available(host: str, port: int, timeout_seconds: float = 10.0) -> None:
    """Best-effort wait for a just-vacated port to actually become bindable
    before starting the real server.

    A server restart (see dashboard/app.py's _shutdown_and_reexec) spawns
    the replacement process before the old one has necessarily finished
    releasing the socket yet -- probing like this absorbs that brief race
    instead of the new process's real bind attempt failing outright and
    leaving the app down. Silently returns once the port binds, or once
    the timeout elapses (the real bind attempt below will then raise its
    own clear error rather than hang)."""
    probe_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind((probe_host, port))
            return
        except OSError:
            time.sleep(0.2)


@dataclass(frozen=True)
class DashboardLaunchConfig:
    """The launch choices shared by CLI and a future packaged entry point."""

    mode: LaunchMode = "none"
    host: str = "127.0.0.1"
    port: int = 8420
    readiness_timeout_seconds: float = 10.0

    @property
    def url(self) -> str:
        host = "127.0.0.1" if self.host in {"0.0.0.0", "::"} else self.host
        return f"http://{host}:{self.port}"


def validate_launch_mode(mode: str) -> LaunchMode:
    """Validate user/config input while keeping the accepted values explicit."""
    if mode not in {"none", "browser", "webview"}:
        raise ValueError(f"Unsupported dashboard launch mode: {mode}")
    return cast(LaunchMode, mode)


def launch_dashboard(app: Any, config: DashboardLaunchConfig) -> None:
    """Run the dashboard and optionally open its local UI.

    ``none`` preserves the existing foreground uvicorn behavior. Browser and
    webview modes run the same ASGI app and stop the server when their UI exits.
    """
    mode = validate_launch_mode(config.mode)
    _wait_for_port_available(config.host, config.port)
    if mode == "none":
        uvicorn.run(app, host=config.host, port=config.port, log_level="warning")
        return

    server = uvicorn.Server(
        uvicorn.Config(app, host=config.host, port=config.port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, name="hanarr-dashboard", daemon=True)
    thread.start()
    deadline = time.monotonic() + config.readiness_timeout_seconds
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=2)
        raise RuntimeError(f"Dashboard did not start at {config.url}")

    try:
        if mode == "browser":
            if not webbrowser.open(config.url):
                raise RuntimeError(f"Could not open a browser for {config.url}")
            thread.join()
        else:
            try:
                import webview
            except ImportError as exc:
                raise RuntimeError(
                    "Desktop webview mode requires the optional dependency. "
                    "Install with `pip install -e \".[desktop]\"`."
                ) from exc
            webview.create_window("Hanarr", config.url)
            webview.start()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
