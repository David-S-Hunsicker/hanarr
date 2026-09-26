"""RemoteOK API — public JSON feed, no auth.

Docs: https://remoteok.com/api
"""
from __future__ import annotations

import datetime as dt

import httpx

from .base import Connector, RawJobPosting, to_naive_utc

API_URL = "https://remoteok.com/api"


class RemoteOKConnector(Connector):
    name = "remoteok"

    def __init__(self, tags: list[str] | None = None):
        self.tags = [t.lower() for t in (tags or [])]

    def fetch(self) -> list[RawJobPosting]:
        try:
            resp = httpx.get(
                API_URL,
                timeout=30.0,
                headers={"User-Agent": "job-search-copilot (personal use)"},
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, OSError):
            return []

        postings: list[RawJobPosting] = []
        for item in data:
            if not isinstance(item, dict) or "id" not in item:
                continue  # first element is a legend/meta object, not a job

            item_tags = [t.lower() for t in item.get("tags", [])]
            if self.tags and not (set(self.tags) & set(item_tags)):
                continue

            posted_at = None
            if item.get("date"):
                try:
                    posted_at = to_naive_utc(dt.datetime.fromisoformat(item["date"].replace("Z", "+00:00")))
                except ValueError:
                    pass

            postings.append(
                RawJobPosting(
                    source=self.name,
                    external_id=str(item["id"]),
                    company=item.get("company", ""),
                    title=item.get("position", ""),
                    location=item.get("location", "") or "Remote",
                    remote=True,
                    url=item.get("url", ""),
                    description=item.get("description", ""),
                    salary_min=_to_float(item.get("salary_min")),
                    salary_max=_to_float(item.get("salary_max")),
                    posted_at=posted_at,
                )
            )
        return postings


def _to_float(val) -> float | None:
    try:
        return float(val) if val else None
    except (TypeError, ValueError):
        return None
