"""Launch the local dashboard in a browser or an optional desktop webview.

This module deliberately owns only the launch contract. Packaging, installer
registration, runtime bundling, and Ollama setup remain separate milestones.
"""
from __future__ import annotations

import threading
import time
import webbrowser
from dataclasses import dataclass
from typing import Any, Literal, cast

import uvicorn

LaunchMode = Literal["none", "browser", "webview"]


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
