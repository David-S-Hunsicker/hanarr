"""Repo-wide test safety net.

A route handler under test can call save_settings_to_yaml(settings,
str(DEFAULT_CONFIG_PATH)) with the *default*, relative "config.yaml" path
-- if a test forgets to monkeypatch.chdir() into a tmp_path first, that
write lands on the real config.yaml at the repo root instead of a
throwaway file. That's a real, previously-hit bug (a schedule-saving test
silently overwrote this developer's actual local config), not a
hypothetical -- this autouse fixture makes it fail loudly and restores
the file instead of leaving it corrupted.
"""
from pathlib import Path

import pytest

_REAL_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


@pytest.fixture(autouse=True)
def _protect_real_config_yaml():
    before = _REAL_CONFIG_PATH.read_bytes() if _REAL_CONFIG_PATH.exists() else None
    yield
    after = _REAL_CONFIG_PATH.read_bytes() if _REAL_CONFIG_PATH.exists() else None
    if after != before:
        if before is None:
            _REAL_CONFIG_PATH.unlink(missing_ok=True)
        else:
            _REAL_CONFIG_PATH.write_bytes(before)
        pytest.fail(
            "A test wrote to the real repo config.yaml instead of a tmp_path -- "
            "it has been restored, but the test needs monkeypatch.chdir(tmp_path) "
            "before any code path that calls save_settings_to_yaml()."
        )
