"""Greenhouse job board API — public, no auth, no API key.

Docs: https://developers.greenhouse.io/job-board.html
Endpoint: https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true

`board_token` is the slug in a company's public board URL, e.g. for
boards.greenhouse.io/stripe it's "stripe".
"""
from __future__ import annotations

import re

import httpx

from .base import Connector, RawJobPosting

API_URL = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs"


class GreenhouseConnector(Connector):
    name = "greenhouse"

    def __init__(self, company_boards: list[str]):
        self.company_boards = company_boards

    def fetch(self) -> list[RawJobPosting]:
        postings: list[RawJobPosting] = []
        for board in self.company_boards:
            try:
                resp = httpx.get(
                    API_URL.format(board=board), params={"content": "true"}, timeout=30.0
                )
                resp.raise_for_status()
                jobs = resp.json().get("jobs", [])
            except httpx.HTTPError:
                continue  # one bad board shouldn't kill the whole search run

            for job in jobs:
                location = (job.get("location") or {}).get("name", "") or ""
                description = _strip_html(job.get("content", ""))
                postings.append(
                    RawJobPosting(
                        source=self.name,
                        external_id=str(job["id"]),
                        company=board,
                        title=job.get("title", ""),
                        location=location,
                        remote="remote" in location.lower(),
                        url=job.get("absolute_url", ""),
                        description=description,
                    )
                )
        return postings


def _strip_html(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html or "").strip()
