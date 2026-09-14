"""Arbeitnow job board API — public, no auth.

Docs: https://arbeitnow.com/api/job-board-api
"""
from __future__ import annotations

import datetime as dt

import httpx

from .base import Connector, RawJobPosting

API_URL = "https://www.arbeitnow.com/api/job-board-api"


class ArbeitnowConnector(Connector):
    name = "arbeitnow"

    def fetch(self) -> list[RawJobPosting]:
        try:
            resp = httpx.get(API_URL, timeout=30.0)
            resp.raise_for_status()
            data = resp.json().get("data", [])
        except httpx.HTTPError:
            return []

        postings: list[RawJobPosting] = []
        for item in data:
            posted_at = None
            if item.get("created_at"):
                try:
                    posted_at = dt.datetime.fromtimestamp(int(item["created_at"]))
                except (TypeError, ValueError, OSError):
                    pass

            postings.append(
                RawJobPosting(
                    source="arbeitnow",
                    external_id=item.get("slug", item.get("url", "")),
                    company=item.get("company_name", ""),
                    title=item.get("title", ""),
                    location=item.get("location", "") or "",
                    remote=bool(item.get("remote", False)),
                    url=item.get("url", ""),
                    description=item.get("description", ""),
                    posted_at=posted_at,
                )
            )
        return postings
