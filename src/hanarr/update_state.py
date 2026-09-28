"""Live auto-update progress/availability, shared between the background
scheduler (which checks for and stages updates) and the dashboard (which
shows a countdown banner and lets the user cancel or apply early).

Same shared-mutable-dict pattern as search_state.py, for the same reason:
one object constructed once in cli.py's serve command and passed to both
start_scheduler() and create_app() so a background-found update shows up
on the dashboard identically no matter which process/thread found it.
"""
from __future__ import annotations

import time
from typing import Any


def new_update_state() -> dict[str, Any]:
    return {
        # True once a newer, verified-checksum release has been found and
        # its installer downloaded -- the dashboard shows the countdown
        # banner exactly when this is true.
        "available": False,
        "version": None,
        "notes_url": None,
        "installer_path": None,
        # Epoch seconds (time.time()) after which the scheduler's apply-tick
        # job will actually run the installer, if not cancelled and no
        # search is running. None until an update is staged.
        "apply_at": None,
        # Set by the dashboard's Cancel button. The apply-tick job checks
        # this and skips applying -- the staged installer and "available"
        # state are left in place so "Install now" in Settings still works
        # (the explicit manual fallback), just not automatically.
        "cancelled": False,
        # True only for the brief window the installer is actually being
        # launched -- distinct from "available", which stays true the whole
        # time an update is staged/pending/cancelled.
        "applying": False,
        "error": None,
        "last_checked_at": None,
    }


def reset_for_update(state: dict[str, Any], *, version: str, notes_url: str, installer_path: str, apply_at: float) -> None:
    state["available"] = True
    state["version"] = version
    state["notes_url"] = notes_url
    state["installer_path"] = installer_path
    state["apply_at"] = apply_at
    state["cancelled"] = False
    state["applying"] = False
    state["error"] = None


def clear(state: dict[str, Any]) -> None:
    """Used after a successful apply (about to restart anyway) or when a
    staged update turns out to be stale/superseded."""
    state["available"] = False
    state["version"] = None
    state["notes_url"] = None
    state["installer_path"] = None
    state["apply_at"] = None
    state["cancelled"] = False
    state["applying"] = False


def mark_checked(state: dict[str, Any]) -> None:
    state["last_checked_at"] = time.time()
