"""Regression tests for import-order-dependent circular imports.

matching.py, connectors/registry.py, and company_categories.py form a
triangle: registry.py imports company_categories.py, which imports a
constant back from matching.py, which (used to) import RawJobPosting from
connectors.base -- pulling in connectors/__init__.py -> registry.py while
matching.py was still mid-import. This only failed depending on which of
the three happened to be imported *first* in a given process, so it passed
in most of the test suite (something else already fully imported matching
first) and only broke a script or entry point that imported one of these
fresh. Each test here runs in its own subprocess so it gets a truly clean
import state, not whatever's already cached in this process from other
tests."""
import subprocess
import sys


def _import_fresh(module: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_matching_imports_cleanly_as_the_first_module():
    _import_fresh("hanarr.matching")


def test_connectors_imports_cleanly_as_the_first_module():
    _import_fresh("hanarr.connectors")


def test_company_categories_imports_cleanly_as_the_first_module():
    _import_fresh("hanarr.company_categories")
