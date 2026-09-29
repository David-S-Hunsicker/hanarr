"""Workday CXS job board API — public, unauthenticated, but *undocumented*:
this is the same internal JSON endpoint a Workday-hosted careers page's own
JavaScript calls when a visitor searches it, not a supported public API the
way Greenhouse/Lever/Ashby are. Workday could change its shape or start
blocking non-browser traffic without notice. Used respectfully here (a
courtesy delay between requests, an identifying User-Agent, and a hard cap
on how many postings/pages one company will pull), matching how widely this
exact endpoint is already used by third-party job aggregators.

Unlike the other connectors, the list endpoint doesn't include a job
description -- only title/location/a requisition id -- so a second request
per job (`GET .../wday/cxs/{tenant}/{site}{externalPath}`) is needed to get
real description text for scoring. That N+1 shape is real cost (a company
with 200 open roles is 201 requests), bounded below by MAX_POSTINGS_PER_SITE.

`career_site_urls` are the company's own public Workday careers URL, e.g.
`https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite` --
tenant/shard/site/locale are parsed out of it, since there's no directory
mapping a company name to these values.
"""
from __future__ import annotations

import html
import re
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from .base import Connector, RawJobPosting

USER_AGENT = "hanarr-job-search (personal use)"

# Workday's list endpoint silently returns an empty jobPostings array (HTTP
# 200, no error) for any limit above 20 -- this is the real server-side cap,
# not a courtesy choice.
PAGE_SIZE = 20

# An undocumented pagination ceiling exists somewhere in the low thousands
# depending on tenant; this caps *our own* request volume well below that,
# both as a courtesy to a shared public endpoint and to keep one huge board
# from making a search run take minutes. Users with a specific need for more
# from one company can raise this later -- there's no dial for it in the UI
# yet since it hasn't come up.
MAX_POSTINGS_PER_SITE = 200

# One request per job's detail on top of the list requests -- keep the
# per-request pause short so a single company with real openings doesn't
# make the whole search noticeably slower, while still not hammering a
# shared public endpoint back-to-back.
REQUEST_DELAY_SECONDS = 0.3

_LOCALE_RE = re.compile(r"^[a-zA-Z]{2}-[a-zA-Z]{2}$")
_HOST_RE = re.compile(r"^(?P<tenant>[a-z0-9-]+)\.(?P<shard>wd\d+)\.myworkdayjobs\.com$", re.IGNORECASE)


@dataclass
class WorkdaySite:
    tenant: str
    shard: str
    site: str
    locale: str
    host: str


def parse_career_site_url(url: str) -> WorkdaySite | None:
    """Returns None (never raises) for anything that doesn't look like a
    real Workday careers URL -- one misconfigured entry in the list must
    not crash the whole connector, same as a failed HTTP request."""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None
    if not parsed.hostname:
        return None
    match = _HOST_RE.match(parsed.hostname)
    if not match:
        return None

    segments = [s for s in parsed.path.split("/") if s]
    locale = "en-US"
    if segments and _LOCALE_RE.match(segments[0]):
        locale = segments[0]
        segments = segments[1:]
    if not segments:
        return None
    site = segments[0]

    return WorkdaySite(
        tenant=match.group("tenant"),
        shard=match.group("shard"),
        site=site,
        locale=locale,
        host=parsed.hostname,
    )


class WorkdayConnector(Connector):
    name = "workday"

    def __init__(self, career_site_urls: list[str]):
        self.career_site_urls = career_site_urls

    def fetch(self) -> list[RawJobPosting]:
        postings: list[RawJobPosting] = []
        for i, url in enumerate(self.career_site_urls):
            if i > 0:
                time.sleep(REQUEST_DELAY_SECONDS)
            site = parse_career_site_url(url)
            if site is None:
                continue  # unparsable entry shouldn't crash the whole search run
            postings.extend(self._fetch_site(site))
        return postings

    def _fetch_site(self, site: WorkdaySite) -> list[RawJobPosting]:
        list_url = f"https://{site.host}/wday/cxs/{site.tenant}/{site.site}/jobs"
        headers = {"User-Agent": USER_AGENT, "Content-Type": "application/json"}

        results: list[RawJobPosting] = []
        offset = 0
        total = None
        first_request = True
        while offset < MAX_POSTINGS_PER_SITE and (total is None or offset < total):
            if not first_request:
                time.sleep(REQUEST_DELAY_SECONDS)
            first_request = False
            try:
                resp = httpx.post(
                    list_url,
                    json={"appliedFacets": {}, "limit": PAGE_SIZE, "offset": offset, "searchText": ""},
                    headers=headers,
                    timeout=30.0,
                )
                resp.raise_for_status()
                data = resp.json()
            except (httpx.HTTPError, OSError, ValueError):
                break  # one bad page shouldn't drop postings already collected

            batch = data.get("jobPostings") or []
            if total is None:
                total = data.get("total", 0)
            if not batch:
                break

            for job in batch:
                posting = self._build_posting(site, job)
                if posting is not None:
                    results.append(posting)
            offset += PAGE_SIZE
        return results

    def _build_posting(self, site: WorkdaySite, job: dict) -> RawJobPosting | None:
        external_path = job.get("externalPath") or ""
        if not external_path:
            return None
        bullet_fields = job.get("bulletFields") or []
        req_id = bullet_fields[0] if bullet_fields else external_path
        location = job.get("locationsText") or ""
        time.sleep(REQUEST_DELAY_SECONDS)
        description = self._fetch_description(site, external_path)

        return RawJobPosting(
            source=self.name,
            external_id=str(req_id),
            company=site.tenant,
            title=job.get("title") or "",
            location=location,
            remote="remote" in location.lower(),
            url=f"https://{site.host}/{site.locale}/{site.site}{external_path}",
            description=description,
            # postedOn in the list response is a localized relative string
            # ("Posted 3 Days Ago"), not a real date, and the detail
            # response's startDate isn't reliably documented as "date
            # posted" vs. "requisition start date" -- guessing would risk
            # silently mislabeling reminders/sort order elsewhere in the
            # app, so this is left unset rather than fabricated.
            posted_at=None,
        )

    def _fetch_description(self, site: WorkdaySite, external_path: str) -> str:
        detail_url = f"https://{site.host}/wday/cxs/{site.tenant}/{site.site}{external_path}"
        try:
            resp = httpx.get(
                detail_url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=30.0
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, OSError, ValueError):
            return ""  # description is best-effort; the job is still worth including

        info = data.get("jobPostingInfo") or data
        return _strip_html(info.get("jobDescription") or "")


def _strip_html(raw: str) -> str:
    # Same entity-escaping issue as the Greenhouse/Lever connectors -- see
    # Greenhouse's _strip_html for the full explanation.
    unescaped = html.unescape(html.unescape(raw or ""))
    stripped = re.sub(r"<[^>]+>", " ", unescaped)
    return re.sub(r"\s+", " ", stripped).strip()
