"""Ashby job board API — public, no auth, no API key.

Docs: https://developers.ashbyhq.com/reference/jobpostingapi
Endpoint: https://api.ashbyhq.com/posting-api/job-board/{board_name}?includeCompensation=true

`board_name` is the slug in a company's public board URL, e.g. for
jobs.ashbyhq.com/ramp it's "ramp".
"""
from __future__ import annotations

import datetime as dt
import time

import httpx

from .base import Connector, RawJobPosting, to_naive_utc

API_URL = "https://api.ashbyhq.com/posting-api/job-board/{board}"

# Same rationale as the Greenhouse/Lever connectors: one request per
# configured company back-to-back, so a short delay keeps a multi-board
# search from looking like a burst against a shared public API.
REQUEST_DELAY_SECONDS = 0.75


class AshbyConnector(Connector):
    name = "ashby"

    def __init__(self, company_boards: list[str]):
        self.company_boards = company_boards

    def fetch(self) -> list[RawJobPosting]:
        postings: list[RawJobPosting] = []
        for i, board in enumerate(self.company_boards):
            if i > 0:
                time.sleep(REQUEST_DELAY_SECONDS)
            try:
                resp = httpx.get(
                    API_URL.format(board=board),
                    params={"includeCompensation": "true"},
                    timeout=30.0,
                )
                resp.raise_for_status()
                jobs = resp.json().get("jobs", [])
            except httpx.HTTPError:
                continue  # one bad board shouldn't kill the whole search run

            for job in jobs:
                if not job.get("isListed", True):
                    continue
                salary_min, salary_max = _extract_usd_salary(job.get("compensation"))
                # descriptionPlain is already plain text -- unlike Greenhouse/
                # Lever's HTML content fields, there's no entity-escaping or
                # tag-stripping bug class to worry about here.
                description = job.get("descriptionPlain") or ""
                postings.append(
                    RawJobPosting(
                        source=self.name,
                        external_id=str(job["id"]),
                        company=board,
                        title=(job.get("title") or "").strip(),
                        location=job.get("location", "") or "",
                        remote=bool(job.get("isRemote")),
                        url=job.get("jobUrl", "") or "",
                        description=description,
                        salary_min=salary_min,
                        salary_max=salary_max,
                        posted_at=_parse_published_at(job.get("publishedAt")),
                    )
                )
        return postings


def _extract_usd_salary(compensation: dict | None) -> tuple[float | None, float | None]:
    """Ashby's compensation payload nests salary inside a list of tiers,
    each with a list of components (salary, equity, bonus, ...). Only a
    USD-denominated Salary component is usable here -- the app has no
    currency-conversion concept, so mixing in other currencies would
    silently compare unrelated numbers against preferences.salary_floor_usd."""
    if not compensation:
        return None, None
    for tier in compensation.get("compensationTiers") or []:
        for component in tier.get("components") or []:
            if component.get("compensationType") == "Salary" and component.get("currencyCode") == "USD":
                return component.get("minValue"), component.get("maxValue")
    return None, None


def _parse_published_at(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return to_naive_utc(dt.datetime.fromisoformat(value))
    except ValueError:
        return None
