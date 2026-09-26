"""Greenhouse job board API — public, no auth, no API key.

Docs: https://developers.greenhouse.io/job-board.html
Endpoint: https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true

`board_token` is the slug in a company's public board URL, e.g. for
boards.greenhouse.io/stripe it's "stripe".
"""
from __future__ import annotations

import datetime as dt
import html
import re
import time

import httpx

from .base import Connector, RawJobPosting, to_naive_utc

API_URL = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs"

# Greenhouse's board API is shared across many companies' job boards, and
# we hit one endpoint per configured company back-to-back — a short delay
# between requests keeps a multi-board search from looking like a burst.
REQUEST_DELAY_SECONDS = 0.75


class GreenhouseConnector(Connector):
    name = "greenhouse"

    def __init__(self, company_boards: list[str]):
        self.company_boards = company_boards

    def fetch(self) -> list[RawJobPosting]:
        postings: list[RawJobPosting] = []
        for i, board in enumerate(self.company_boards):
            if i > 0:
                time.sleep(REQUEST_DELAY_SECONDS)
            try:
                resp = httpx.get(
                    API_URL.format(board=board), params={"content": "true"}, timeout=30.0
                )
                resp.raise_for_status()
                jobs = resp.json().get("jobs", [])
            except (httpx.HTTPError, OSError):
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
                        posted_at=_parse_first_published(job.get("first_published")),
                    )
                )
        return postings


def _strip_html(raw: str) -> str:
    # Greenhouse's `content` field is HTML whose own tag delimiters are
    # entity-escaped (`&lt;div class=&quot;...&quot;&gt;`), at least for
    # some postings (seen on Indeed-syndicated listings) -- so stripping
    # literal <tags> first leaves the entire escaped markup untouched,
    # dumping thousands of characters of &lt;/&amp;/&quot; noise into the
    # LLM prompt. Some content is escaped twice (e.g. "&amp;nbsp;"), so
    # unescape is applied twice -- a second pass on already-clean text is a
    # no-op, so this is safe either way. Collapsing whitespace afterward
    # also shrinks the prompt, since every removed tag otherwise leaves
    # its surrounding whitespace behind.
    unescaped = html.unescape(html.unescape(raw or ""))
    stripped = re.sub(r"<[^>]+>", " ", unescaped)
    return re.sub(r"\s+", " ", stripped).strip()


def _parse_first_published(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return to_naive_utc(dt.datetime.fromisoformat(value))
    except ValueError:
        return None
