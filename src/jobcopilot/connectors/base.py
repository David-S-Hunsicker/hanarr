"""Connector interface. Every job source implements `fetch()` and returns
a list of RawJobPosting. Adding a new source means adding one file here
and registering it in registry.py — nothing else in the app needs to
change, which is the point: this is the extension seam for contributors
(and for a future business, the seam for adding paid/private sources).

Only sources with legitimate public APIs/feeds belong here. Do not add a
connector that scrapes a site whose Terms of Service prohibit it (e.g.
LinkedIn, Indeed) — that risks the end user's account and isn't something
this project takes on.
"""
from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class RawJobPosting:
    source: str
    external_id: str
    company: str
    title: str
    location: str
    remote: bool
    url: str
    description: str
    salary_min: float | None = None
    salary_max: float | None = None
    posted_at: dt.datetime | None = None


class Connector(ABC):
    name: str

    @abstractmethod
    def fetch(self) -> list[RawJobPosting]:
        """Fetch current postings from this source. Should raise on hard
        failure but never partially-crash the whole search run — callers
        catch and log per-connector errors."""
        raise NotImplementedError


def to_naive_utc(value: dt.datetime) -> dt.datetime:
    """Normalizes a datetime to naive UTC — the convention every posted_at
    (and every other datetime in this codebase) uses, so values from
    different connectors are directly comparable/sortable. A naive input is
    assumed to already be UTC (source APIs that return unzoned timestamps
    are documented as UTC); an aware input is converted."""
    if value.tzinfo is not None:
        value = value.astimezone(dt.timezone.utc)
    return value.replace(tzinfo=None)
