from __future__ import annotations

from ..config import SourcesConfig
from .arbeitnow import ArbeitnowConnector
from .base import Connector
from .greenhouse import GreenhouseConnector
from .lever import LeverConnector
from .remoteok import RemoteOKConnector


def build_enabled_connectors(sources: SourcesConfig) -> list[Connector]:
    """New source? Add it here once it's implemented as a Connector."""
    connectors: list[Connector] = []
    if sources.greenhouse.enabled and sources.greenhouse.company_boards:
        connectors.append(GreenhouseConnector(sources.greenhouse.company_boards))
    if sources.lever.enabled and sources.lever.companies:
        connectors.append(LeverConnector(sources.lever.companies))
    if sources.remoteok.enabled:
        connectors.append(RemoteOKConnector(sources.remoteok.tags))
    if sources.arbeitnow.enabled:
        connectors.append(ArbeitnowConnector())
    return connectors
