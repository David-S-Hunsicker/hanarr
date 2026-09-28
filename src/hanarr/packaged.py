"""Entry points used by the packaged Windows runtime.

The installer keeps application files replaceable under LocalAppData while
placing configuration, resumes, and SQLite data in a stable user-data folder.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from .cli import cli

USER_DATA_DIR = Path.home() / "AppData" / "Local" / "Hanarr"

# Kept alive for the process lifetime once set -- see _hold_single_instance_mutex.
_mutex_handle = None


def _hold_single_instance_mutex() -> None:
    """Creates (and holds for the life of this process) a named Win32
    mutex matching installer/hanarr.iss's AppMutex directive, so Inno
    Setup can detect this app is running during an update install and
    close it (CloseApplications=yes) -- and relaunch it afterward
    (RestartApplications=yes) -- rather than the install failing on
    locked files or silently leaving the old version running alongside
    the new one. Best-effort: any failure here (e.g. ctypes/Win32
    unavailable) just means that detection doesn't work, not that the
    app fails to start."""
    global _mutex_handle
    try:
        import ctypes

        _mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, False, "HanarrSingleInstanceMutex")
    except (AttributeError, OSError):
        pass


def _bundle_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))


def prepare_user_data() -> Path:
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    (USER_DATA_DIR / "resumes").mkdir(exist_ok=True)
    (USER_DATA_DIR / "data").mkdir(exist_ok=True)
    config_path = USER_DATA_DIR / "config.yaml"
    template = _bundle_root() / "config.example.yaml"
    if not config_path.exists() and template.exists():
        shutil.copyfile(template, config_path)
    os.chdir(USER_DATA_DIR)
    return config_path


def _ensure_standard_streams() -> None:
    """A PyInstaller ``--windowed`` build has no console, so ``sys.stdout``
    and ``sys.stderr`` are ``None`` rather than a stream. Anything that calls
    ``.isatty()`` or writes to them without checking (uvicorn's default
    logging formatter does both) crashes with ``AttributeError``/``ValueError``
    at startup. Give both a real, discarding stream instead.
    """
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")


def run(mode: str) -> None:
    _ensure_standard_streams()
    _hold_single_instance_mutex()
    config_path = prepare_user_data()
    cli(["--config", str(config_path), "serve", "--launch-mode", mode], standalone_mode=False)


def browser() -> None:
    run("browser")


def desktop() -> None:
    run("webview")
