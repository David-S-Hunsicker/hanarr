"""Lever job board API — public, no auth, no API key.

Docs: https://github.com/lever/postings-api
Endpoint: https://api.lever.co/v0/postings/{company}?mode=json

`company` is the slug in a company's public postings URL, e.g. for
jobs.lever.co/netflix it's "netflix".
"""
from __future__ import annotations

import datetime as dt
import html
import re
import time

import httpx

from .base import Connector, RawJobPosting, to_naive_utc

API_URL = "https://api.lever.co/v0/postings/{company}"

# Same courtesy delay as the Greenhouse connector — one request per
# configured company, so a short pause keeps a multi-company search from
# looking like a burst against Lever's shared API.
REQUEST_DELAY_SECONDS = 0.75


class LeverConnector(Connector):
    name = "lever"

    def __init__(self, companies: list[str]):
        self.companies = companies

    def fetch(self) -> list[RawJobPosting]:
        postings: list[RawJobPosting] = []
        for i, company in enumerate(self.companies):
            if i > 0:
                time.sleep(REQUEST_DELAY_SECONDS)
            try:
                resp = httpx.get(
                    API_URL.format(company=company), params={"mode": "json"}, timeout=30.0
                )
                resp.raise_for_status()
                jobs = resp.json()
            except httpx.HTTPError:
                continue  # one bad company shouldn't kill the whole search run

            for job in jobs:
                categories = job.get("categories", {}) or {}
                location = categories.get("location", "") or ""
                description = _strip_html(job.get("descriptionPlain") or job.get("description", ""))
                postings.append(
                    RawJobPosting(
                        source=self.name,
                        external_id=job.get("id", ""),
                        company=company,
                        title=job.get("text", ""),
                        location=location,
                        remote="remote" in location.lower(),
                        url=job.get("hostedUrl", ""),
                        description=description,
                        posted_at=_parse_created_at(job.get("createdAt")),
                    )
                )
        return postings


def _strip_html(raw: str) -> str:
    # Same entity-escaping issue as the Greenhouse connector -- see its
    # _strip_html for the full explanation. Kept as a duplicate here rather
    # than shared, matching this module's existing self-contained style.
    unescaped = html.unescape(html.unescape(raw or ""))
    stripped = re.sub(r"<[^>]+>", " ", unescaped)
    return re.sub(r"\s+", " ", stripped).strip()


def _parse_created_at(value) -> dt.datetime | None:
    if not value:
        return None
    try:
        return to_naive_utc(dt.datetime.fromtimestamp(int(value) / 1000, tz=dt.timezone.utc))
    except (TypeError, ValueError, OSError):
        return None
