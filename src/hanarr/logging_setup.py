"""File-backed logging for the packaged desktop build.

The packaged Windows executable runs with --windowed (see
scripts/build_windows.ps1) -- there's no console, so anything sent to
stderr/stdout is simply discarded. Before this, an unhandled exception
during `hanarr serve` was unrecoverable to diagnose: a user running the
standalone app had nothing to report back beyond "Internal Server Error",
with zero trace of what actually failed. A rotating file under the data
directory gives every run (packaged or not) a durable, bounded record to
check after something goes wrong.
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_file_logging(data_dir: Path) -> Path:
    """Adds a rotating file handler to the root logger, alongside whatever
    console handler logging.basicConfig() already attached in the CLI
    group callback (harmless/invisible in the packaged --windowed build,
    still useful when run from a real terminal). Returns the log file path.
    """
    log_dir = Path(data_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "hanarr.log"

    handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logging.getLogger().addHandler(handler)
    return log_path
