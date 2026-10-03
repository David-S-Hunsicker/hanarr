"""Workable's public cross-employer job search — public, no auth, no API key.

Docs: this is the same API https://jobs.workable.com (Workable's own public
job search site) calls from its own frontend, not a documented/versioned
partner API the way Greenhouse/Lever/Ashby are, but it's the same trust
level as Workday's connector (a real public endpoint backing a public page,
used respectfully: a courtesy delay, an identifying User-Agent).

Unlike Greenhouse/Lever/Ashby/Recruitee, Workable has no usable per-company
"board token" concept here: most individual company accounts
(www.workable.com/api/accounts/<slug>) return zero current postings even
for well-known names, since Workable skews toward smaller employers with no
stable public directory of slugs to sample from the way the other sources'
default lists were built. Instead this connector queries Workable's global
search by keyword (the same thing typing into jobs.workable.com's own
search box does), shaped like RemoteOKConnector's tags.

The search endpoint caps at 20 results per request (a server-side limit,
not a courtesy choice -- `limit` above 20 errors) and its pagination token
isn't documented, so this takes the first page per query term rather than
guessing at how to page through it.
"""
from __future__ import annotations

import datetime as dt
import html
import re
import time

import httpx

from .base import Connector, RawJobPosting, to_naive_utc

API_URL = "https://jobs.workable.com/api/v1/jobs"
PAGE_SIZE = 20
USER_AGENT = "hanarr-job-search (personal use)"

# One request per configured query term -- a short delay keeps multiple
# terms from hitting the same shared public endpoint back-to-back.
REQUEST_DELAY_SECONDS = 0.75


class WorkableConnector(Connector):
    name = "workable"

    def __init__(self, queries: list[str]):
        self.queries = queries

    def fetch(self) -> list[RawJobPosting]:
        postings: list[RawJobPosting] = []
        for i, query in enumerate(self.queries):
            if i > 0:
                time.sleep(REQUEST_DELAY_SECONDS)
            try:
                resp = httpx.get(
                    API_URL,
                    params={"query": query, "limit": PAGE_SIZE},
                    headers={"Accept": "application/json", "User-Agent": USER_AGENT},
                    timeout=30.0,
                )
                resp.raise_for_status()
                jobs = resp.json().get("jobs", [])
            except (httpx.HTTPError, OSError, ValueError):
                continue  # one bad query shouldn't kill the whole search run

            for job in jobs:
                postings.append(_build_posting(job))
        return postings


def _build_posting(job: dict) -> RawJobPosting:
    company = (job.get("company") or {}).get("title", "")
    locations = job.get("locations") or []
    location = "; ".join(locations) if locations else ""
    workplace = (job.get("workplace") or "").lower()
    description = " ".join(
        _strip_html(job.get(field, ""))
        for field in ("description", "requirementsSection")
        if job.get(field)
    )
    return RawJobPosting(
        source="workable",
        external_id=str(job.get("id", "")),
        company=company,
        title=job.get("title", ""),
        location=location,
        remote=workplace == "remote" or "remote" in location.lower(),
        url=job.get("url", ""),
        description=description,
        posted_at=_parse_created(job.get("created")),
    )


def _strip_html(raw: str) -> str:
    # Same entity-escaping issue as Greenhouse's connector -- see its
    # _strip_html for the full explanation.
    unescaped = html.unescape(html.unescape(raw or ""))
    stripped = re.sub(r"<[^>]+>", " ", unescaped)
    return re.sub(r"\s+", " ", stripped).strip()


def _parse_created(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return to_naive_utc(dt.datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None
