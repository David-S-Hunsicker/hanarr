"""USAJOBS Search API — the official, documented REST API for the US federal
government's job board, operated by the Office of Personnel Management.

Docs: https://developer.usajobs.gov/api-reference/get-api-search
Endpoint: https://data.usajobs.gov/api/search

Unlike Greenhouse/Lever/Ashby/Recruitee/Workable, this requires every caller
to register for a free API key at developer.usajobs.gov/apirequest, tied to
an email address -- there's no anonymous/shared access, and every request
must send that email as the User-Agent header and the key as
Authorization-Key (confirmed live: an invalid key returns a 401, not an
anonymous-allowed response). See config.py's UsajobsSource for how the key
is stored (OS keyring, same treatment as the Anthropic API key) and
user_agent_email (plain config, not a secret).

Reaches a candidate pool none of this app's other sources touch at all:
federal and related public-sector postings across every US government
agency, not more of the same startup/tech-board postings.
"""
from __future__ import annotations

import datetime as dt
import time

import httpx

from .base import Connector, RawJobPosting, to_naive_utc

API_URL = "https://data.usajobs.gov/api/search"
RESULTS_PER_PAGE = 100

# One request per configured query term -- a short delay keeps multiple
# terms from hitting the API back-to-back.
REQUEST_DELAY_SECONDS = 0.5


class UsajobsConnector(Connector):
    name = "usajobs"

    def __init__(self, queries: list[str], *, user_agent_email: str, api_key: str):
        self.queries = queries
        self.user_agent_email = user_agent_email
        self.api_key = api_key

    def fetch(self) -> list[RawJobPosting]:
        if not self.user_agent_email or not self.api_key:
            return []  # not configured -- no credentials to call with

        headers = {
            "Host": "data.usajobs.gov",
            "User-Agent": self.user_agent_email,
            "Authorization-Key": self.api_key,
        }
        postings: list[RawJobPosting] = []
        for i, query in enumerate(self.queries):
            if i > 0:
                time.sleep(REQUEST_DELAY_SECONDS)
            try:
                resp = httpx.get(
                    API_URL,
                    params={"Keyword": query, "ResultsPerPage": RESULTS_PER_PAGE},
                    headers=headers,
                    timeout=30.0,
                )
                resp.raise_for_status()
                items = resp.json().get("SearchResult", {}).get("SearchResultItems", [])
            except (httpx.HTTPError, OSError, ValueError):
                continue  # one bad query shouldn't kill the whole search run

            for item in items:
                posting = _build_posting(item.get("MatchedObjectDescriptor", {}))
                if posting is not None:
                    postings.append(posting)
        return postings


def _build_posting(descriptor: dict) -> RawJobPosting | None:
    position_id = descriptor.get("PositionID")
    if not position_id:
        return None

    location_display = descriptor.get("PositionLocationDisplay") or ""
    remuneration = (descriptor.get("PositionRemuneration") or [{}])[0]
    details = (descriptor.get("UserArea") or {}).get("Details") or {}
    description = " ".join(
        text for text in (details.get("JobSummary"), descriptor.get("QualificationSummary")) if text
    )
    apply_uris = descriptor.get("ApplyURI") or []

    return RawJobPosting(
        source="usajobs",
        external_id=str(position_id),
        company=descriptor.get("OrganizationName") or descriptor.get("DepartmentName") or "",
        title=descriptor.get("PositionTitle", ""),
        location=location_display,
        remote="remote" in location_display.lower() or "anywhere" in location_display.lower(),
        url=apply_uris[0] if apply_uris else descriptor.get("PositionURI", ""),
        description=description,
        salary_min=_to_float(remuneration.get("MinimumRange")),
        salary_max=_to_float(remuneration.get("MaximumRange")),
        posted_at=_parse_date(descriptor.get("PublicationStartDate")),
    )


def _to_float(value) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _parse_date(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return to_naive_utc(dt.datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None
