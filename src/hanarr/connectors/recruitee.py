"""Recruitee careers-site API — public, no auth, no API key.

Docs confirmed live against real company subdomains (see config.example.yaml's
recruitee.company_boards for the exact ones verified): a 200 from
https://<company>.recruitee.com/api/offers/ means it's real, an empty
`offers` list or a 404 means the slug is wrong or that company doesn't use
Recruitee's public careers API.

`company` is the subdomain slug in a company's public careers URL, e.g. for
vandebron.recruitee.com it's "vandebron".
"""
from __future__ import annotations

import datetime as dt
import html
import re
import time

import httpx

from .base import Connector, RawJobPosting, to_naive_utc

API_URL = "https://{company}.recruitee.com/api/offers/"

# Same reasoning as Greenhouse's connector: one endpoint per configured
# company hit back-to-back across a multi-board search, so a short delay
# keeps that from looking like a burst against a shared public API.
REQUEST_DELAY_SECONDS = 0.75


class RecruiteeConnector(Connector):
    name = "recruitee"

    def __init__(self, company_boards: list[str]):
        self.company_boards = company_boards

    def fetch(self) -> list[RawJobPosting]:
        postings: list[RawJobPosting] = []
        for i, company in enumerate(self.company_boards):
            if i > 0:
                time.sleep(REQUEST_DELAY_SECONDS)
            try:
                resp = httpx.get(API_URL.format(company=company), timeout=30.0)
                resp.raise_for_status()
                offers = resp.json().get("offers", [])
            except (httpx.HTTPError, OSError, ValueError):
                continue  # one bad board shouldn't kill the whole search run

            for offer in offers:
                postings.append(
                    RawJobPosting(
                        source=self.name,
                        external_id=str(offer.get("id", "")),
                        company=offer.get("company_name") or company,
                        title=offer.get("title", ""),
                        location=offer.get("location") or "",
                        remote=bool(offer.get("remote", False)),
                        url=offer.get("careers_url", ""),
                        description=_strip_html(offer.get("description", "")),
                        posted_at=_parse_published_at(offer.get("published_at")),
                    )
                )
        return postings


def _strip_html(raw: str) -> str:
    # Same entity-escaping issue as Greenhouse's connector -- see its
    # _strip_html for the full explanation.
    unescaped = html.unescape(html.unescape(raw or ""))
    stripped = re.sub(r"<[^>]+>", " ", unescaped)
    return re.sub(r"\s+", " ", stripped).strip()


def _parse_published_at(value: str | None) -> dt.datetime | None:
    # Recruitee's timestamps are "YYYY-MM-DD HH:MM:SS UTC", not ISO 8601 --
    # a literal trailing zone name rather than an offset, so fromisoformat
    # can't parse it directly.
    if not value:
        return None
    try:
        naive = dt.datetime.strptime(value.removesuffix(" UTC"), "%Y-%m-%d %H:%M:%S")
        return to_naive_utc(naive.replace(tzinfo=dt.timezone.utc))
    except ValueError:
        return None
