from __future__ import annotations

from ..company_categories import categories_for_profile, filter_boards
from ..config import Preferences, SourcesConfig
from .arbeitnow import ArbeitnowConnector
from .ashby import AshbyConnector
from .base import Connector
from .greenhouse import GreenhouseConnector
from .lever import LeverConnector
from .remoteok import RemoteOKConnector


def build_enabled_connectors(
    sources: SourcesConfig,
    *,
    resume_summary: dict | None = None,
    preferences: Preferences | None = None,
) -> list[Connector]:
    """New source? Add it here once it's implemented as a Connector.

    resume_summary/preferences are optional so existing callers that just
    want a plain enabled-connector list (e.g. counting sources for a
    progress bar before a specific profile is loaded) keep working
    unchanged; passing both enables profile-based filtering of the
    per-company board lists (see company_categories.py) when
    sources.filter_boards_by_profile is on."""
    profile_categories = (
        categories_for_profile(resume_summary or {}, preferences)
        if sources.filter_boards_by_profile and preferences is not None
        else frozenset()
    )

    def _boards(company_slugs: list[str]) -> list[str]:
        if not profile_categories:
            return company_slugs
        return filter_boards(company_slugs, profile_categories)

    connectors: list[Connector] = []
    if sources.greenhouse.enabled and sources.greenhouse.company_boards:
        boards = _boards(sources.greenhouse.company_boards)
        if boards:
            connectors.append(GreenhouseConnector(boards))
    if sources.lever.enabled and sources.lever.companies:
        companies = _boards(sources.lever.companies)
        if companies:
            connectors.append(LeverConnector(companies))
    if sources.ashby.enabled and sources.ashby.company_boards:
        boards = _boards(sources.ashby.company_boards)
        if boards:
            connectors.append(AshbyConnector(boards))
    if sources.remoteok.enabled:
        connectors.append(RemoteOKConnector(sources.remoteok.tags))
    if sources.arbeitnow.enabled:
        connectors.append(ArbeitnowConnector())
    return connectors
