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


def run(mode: str) -> None:
    config_path = prepare_user_data()
    cli(["--config", str(config_path), "serve", "--launch-mode", mode], standalone_mode=False)


def browser() -> None:
    run("browser")


def desktop() -> None:
    run("webview")
