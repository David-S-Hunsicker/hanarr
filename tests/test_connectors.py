import datetime as dt

import httpx
import respx

from hanarr.connectors.arbeitnow import ArbeitnowConnector
from hanarr.connectors.ashby import AshbyConnector
from hanarr.connectors.base import to_naive_utc
from hanarr.connectors.greenhouse import GreenhouseConnector
from hanarr.connectors.lever import LeverConnector
from hanarr.connectors.registry import build_enabled_connectors
from hanarr.connectors.remoteok import RemoteOKConnector
from hanarr.connectors.workday import WorkdayConnector, parse_career_site_url
from hanarr.config import Settings


def test_to_naive_utc_converts_aware_offset_datetime():
    aware = dt.datetime(2026, 9, 9, 10, 50, 29, tzinfo=dt.timezone(dt.timedelta(hours=-4)))
    result = to_naive_utc(aware)
    assert result == dt.datetime(2026, 9, 9, 14, 50, 29)
    assert result.tzinfo is None


def test_to_naive_utc_strips_tzinfo_from_aware_utc_datetime():
    aware_utc = dt.datetime(2026, 9, 9, 14, 50, 29, tzinfo=dt.timezone.utc)
    result = to_naive_utc(aware_utc)
    assert result == dt.datetime(2026, 9, 9, 14, 50, 29)
    assert result.tzinfo is None


def test_to_naive_utc_passes_through_naive_datetime_unchanged():
    naive = dt.datetime(2026, 9, 9, 14, 50, 29)
    result = to_naive_utc(naive)
    assert result == naive
    assert result.tzinfo is None


@respx.mock
def test_greenhouse_connector_parses_jobs():
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").mock(
        return_value=httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": 123,
                        "title": "Backend Engineer",
                        "location": {"name": "Remote - US"},
                        "absolute_url": "https://boards.greenhouse.io/acme/jobs/123",
                        "content": "<p>We build things.</p>",
                    }
                ]
            },
        )
    )
    connector = GreenhouseConnector(company_boards=["acme"])
    postings = connector.fetch()

    assert len(postings) == 1
    assert postings[0].external_id == "123"
    assert postings[0].remote is True
    assert "We build things." in postings[0].description


@respx.mock
def test_greenhouse_connector_unescapes_entity_encoded_html_content():
    """Regression test: some postings (seen on Indeed-syndicated Greenhouse
    listings, e.g. Coinbase) come back with their own tag delimiters
    entity-escaped ("&lt;div&gt;" instead of "<div>"), sometimes doubly so
    ("&amp;nbsp;"). Stripping literal <tags> first left that markup
    completely untouched -- thousands of characters of &lt;/&amp;/&quot;
    noise landed in the LLM fit-scoring prompt verbatim, which was enough
    to make the model return malformed JSON and silently fall back to the
    rule-based scorer on every posting from these companies."""
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").mock(
        return_value=httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": 123,
                        "title": "Backend Engineer",
                        "location": {"name": "Remote - US"},
                        "absolute_url": "https://boards.greenhouse.io/acme/jobs/123",
                        "content": (
                            "&lt;div class=&quot;content&quot;&gt;&lt;p&gt;We build "
                            "things &amp;amp; ship fast.&amp;nbsp;&lt;/p&gt;&lt;/div&gt;"
                        ),
                    }
                ]
            },
        )
    )
    connector = GreenhouseConnector(company_boards=["acme"])
    postings = connector.fetch()

    description = postings[0].description
    assert "&lt;" not in description
    assert "&amp;" not in description
    assert "&quot;" not in description
    assert "&nbsp;" not in description
    assert "We build things & ship fast." in description


@respx.mock
def test_greenhouse_connector_parses_first_published_as_naive_utc():
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").mock(
        return_value=httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": 123,
                        "title": "Backend Engineer",
                        "location": {"name": "Remote - US"},
                        "absolute_url": "https://boards.greenhouse.io/acme/jobs/123",
                        "content": "<p>We build things.</p>",
                        "first_published": "2026-09-09T10:50:29-04:00",
                    }
                ]
            },
        )
    )
    connector = GreenhouseConnector(company_boards=["acme"])
    postings = connector.fetch()

    posted_at = postings[0].posted_at
    assert posted_at is not None
    assert posted_at.tzinfo is None
    assert posted_at == dt.datetime(2026, 9, 9, 14, 50, 29)  # -04:00 converted to UTC


@respx.mock
def test_greenhouse_connector_handles_missing_first_published():
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").mock(
        return_value=httpx.Response(
            200,
            json={"jobs": [{"id": 123, "title": "Backend Engineer", "absolute_url": "u", "content": ""}]},
        )
    )
    connector = GreenhouseConnector(company_boards=["acme"])
    postings = connector.fetch()
    assert postings[0].posted_at is None


@respx.mock
def test_greenhouse_connector_skips_failed_board_without_crashing():
    respx.get("https://boards-api.greenhouse.io/v1/boards/gone/jobs").mock(
        return_value=httpx.Response(404)
    )
    connector = GreenhouseConnector(company_boards=["gone"])
    assert connector.fetch() == []


@respx.mock
def test_ashby_connector_parses_jobs():
    respx.get("https://api.ashbyhq.com/posting-api/job-board/acme").mock(
        return_value=httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": "abc-123",
                        "title": "Backend Engineer",
                        "location": "Remote (US)",
                        "isRemote": True,
                        "isListed": True,
                        "jobUrl": "https://jobs.ashbyhq.com/acme/abc-123",
                        "descriptionPlain": "We build things.",
                        "publishedAt": "2026-01-01T00:00:00.000Z",
                    }
                ]
            },
        )
    )
    connector = AshbyConnector(company_boards=["acme"])
    postings = connector.fetch()

    assert len(postings) == 1
    assert postings[0].external_id == "abc-123"
    assert postings[0].remote is True
    assert postings[0].description == "We build things."
    assert postings[0].posted_at == dt.datetime(2026, 1, 1)


@respx.mock
def test_ashby_connector_skips_unlisted_jobs():
    respx.get("https://api.ashbyhq.com/posting-api/job-board/acme").mock(
        return_value=httpx.Response(
            200,
            json={"jobs": [{"id": "1", "title": "Old role", "isListed": False}]},
        )
    )
    connector = AshbyConnector(company_boards=["acme"])
    assert connector.fetch() == []


@respx.mock
def test_ashby_connector_extracts_usd_salary_and_ignores_other_currencies():
    respx.get("https://api.ashbyhq.com/posting-api/job-board/acme").mock(
        return_value=httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": "1", "title": "Engineer", "isListed": True,
                        "compensation": {
                            "compensationTiers": [
                                {
                                    "components": [
                                        {"compensationType": "EquityPercentage", "currencyCode": None},
                                        {"compensationType": "Salary", "currencyCode": "EUR", "minValue": 80000, "maxValue": 100000},
                                        {"compensationType": "Salary", "currencyCode": "USD", "minValue": 150000, "maxValue": 200000},
                                    ],
                                }
                            ],
                        },
                    }
                ]
            },
        )
    )
    connector = AshbyConnector(company_boards=["acme"])
    postings = connector.fetch()

    assert postings[0].salary_min == 150000
    assert postings[0].salary_max == 200000


@respx.mock
def test_ashby_connector_skips_failed_board_without_crashing():
    respx.get("https://api.ashbyhq.com/posting-api/job-board/gone").mock(
        return_value=httpx.Response(404)
    )
    connector = AshbyConnector(company_boards=["gone"])
    assert connector.fetch() == []


@respx.mock
def test_remoteok_connector_skips_legend_row_and_filters_tags():
    respx.get("https://remoteok.com/api").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"legend": "this first row has no id"},
                {
                    "id": "999",
                    "position": "Python Developer",
                    "company": "RemoteCo",
                    "tags": ["python", "backend"],
                    "url": "https://remoteok.com/remote-jobs/999",
                    "description": "desc",
                    "date": "2026-01-01T00:00:00",
                },
                {
                    "id": "1000",
                    "position": "Sales Rep",
                    "company": "OtherCo",
                    "tags": ["sales"],
                    "url": "https://remoteok.com/remote-jobs/1000",
                },
            ],
        )
    )
    connector = RemoteOKConnector(tags=["python"])
    postings = connector.fetch()

    assert len(postings) == 1
    assert postings[0].company == "RemoteCo"


@respx.mock
def test_remoteok_connector_skips_board_without_crashing_on_oserror():
    """Regression test: on machines running TLS-inspecting security software,
    httpx can raise a plain OSError/PermissionError (not an httpx.HTTPError)
    even for http:// requests, while honoring SSLKEYLOGFILE during TLS
    context setup. One bad board's connection failure shouldn't crash the
    whole search run."""
    respx.get("https://remoteok.com/api").mock(side_effect=PermissionError("Permission denied"))
    connector = RemoteOKConnector()
    assert connector.fetch() == []


@respx.mock
def test_remoteok_connector_parses_date_as_naive_utc():
    respx.get("https://remoteok.com/api").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"legend": "this first row has no id"},
                {
                    "id": "999",
                    "position": "Python Developer",
                    "company": "RemoteCo",
                    "tags": [],
                    "url": "https://remoteok.com/remote-jobs/999",
                    "description": "desc",
                    "date": "2026-09-09T13:00:00Z",
                },
            ],
        )
    )
    connector = RemoteOKConnector()
    postings = connector.fetch()

    posted_at = postings[0].posted_at
    assert posted_at is not None
    assert posted_at.tzinfo is None
    assert posted_at == dt.datetime(2026, 9, 9, 13, 0, 0)


@respx.mock
def test_lever_connector_parses_jobs():
    respx.get("https://api.lever.co/v0/postings/acme").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": "abc-123",
                    "text": "Backend Engineer",
                    "categories": {"location": "Remote - US"},
                    "hostedUrl": "https://jobs.lever.co/acme/abc-123",
                    "descriptionPlain": "We build things.",
                }
            ],
        )
    )
    connector = LeverConnector(companies=["acme"])
    postings = connector.fetch()

    assert len(postings) == 1
    assert postings[0].external_id == "abc-123"
    assert postings[0].remote is True
    assert "We build things." in postings[0].description


@respx.mock
def test_lever_connector_unescapes_entity_encoded_html_content():
    """Same regression as the Greenhouse connector -- see its equivalent
    test for the full explanation."""
    respx.get("https://api.lever.co/v0/postings/acme").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": "abc-123",
                    "text": "Backend Engineer",
                    "categories": {"location": "Remote - US"},
                    "hostedUrl": "https://jobs.lever.co/acme/abc-123",
                    "descriptionPlain": "&lt;p&gt;We build things &amp;amp; ship fast.&lt;/p&gt;",
                }
            ],
        )
    )
    connector = LeverConnector(companies=["acme"])
    postings = connector.fetch()

    description = postings[0].description
    assert "&lt;" not in description
    assert "&amp;" not in description
    assert "We build things & ship fast." in description


@respx.mock
def test_lever_connector_parses_created_at_as_naive_utc():
    respx.get("https://api.lever.co/v0/postings/acme").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": "abc-123",
                    "text": "Backend Engineer",
                    "categories": {"location": "Remote - US"},
                    "hostedUrl": "https://jobs.lever.co/acme/abc-123",
                    "descriptionPlain": "We build things.",
                    "createdAt": 1757437200000,  # 2025-09-09T17:00:00Z
                }
            ],
        )
    )
    connector = LeverConnector(companies=["acme"])
    postings = connector.fetch()

    posted_at = postings[0].posted_at
    assert posted_at is not None
    assert posted_at.tzinfo is None
    assert posted_at == dt.datetime(2025, 9, 9, 17, 0, 0)


@respx.mock
def test_lever_connector_skips_failed_company_without_crashing():
    respx.get("https://api.lever.co/v0/postings/gone").mock(
        return_value=httpx.Response(404)
    )
    connector = LeverConnector(companies=["gone"])
    assert connector.fetch() == []


@respx.mock
def test_arbeitnow_connector_parses_jobs():
    respx.get("https://www.arbeitnow.com/api/job-board-api").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "slug": "acme-backend-engineer",
                        "company_name": "Acme",
                        "title": "Backend Engineer",
                        "location": "Berlin",
                        "remote": False,
                        "url": "https://arbeitnow.com/jobs/acme-backend-engineer",
                        "description": "desc",
                        "created_at": 1700000000,
                    }
                ]
            },
        )
    )
    connector = ArbeitnowConnector()
    postings = connector.fetch()

    assert len(postings) == 1
    assert postings[0].company == "Acme"
    assert postings[0].remote is False
    # Regression check: created_at is UTC epoch seconds. fromtimestamp()
    # without tz= previously interpreted it in the local system timezone,
    # silently shifting posted_at by whatever the host's UTC offset was.
    assert postings[0].posted_at == dt.datetime(2023, 11, 14, 22, 13, 20)
    assert postings[0].posted_at.tzinfo is None


def test_build_enabled_connectors_includes_ashby_when_configured():
    settings = Settings()
    settings.sources.ashby.enabled = True
    settings.sources.ashby.company_boards = ["acme"]

    connectors = build_enabled_connectors(settings.sources)

    assert any(isinstance(c, AshbyConnector) for c in connectors)


def test_build_enabled_connectors_skips_ashby_without_company_boards():
    settings = Settings()
    settings.sources.ashby.enabled = True
    settings.sources.ashby.company_boards = []

    connectors = build_enabled_connectors(settings.sources)

    assert not any(isinstance(c, AshbyConnector) for c in connectors)


def test_build_enabled_connectors_without_a_profile_queries_every_configured_board():
    """Callers that don't pass resume_summary/preferences (e.g. a plain
    source count before a specific profile is loaded) must see the
    original, unfiltered behavior."""
    settings = Settings()
    settings.sources.greenhouse.enabled = True
    settings.sources.greenhouse.company_boards = ["vercel", "stripe"]

    connectors = build_enabled_connectors(settings.sources)

    greenhouse = next(c for c in connectors if isinstance(c, GreenhouseConnector))
    assert greenhouse.company_boards == ["vercel", "stripe"]


def test_build_enabled_connectors_filters_boards_by_profile_when_enabled():
    settings = Settings()
    settings.sources.greenhouse.enabled = True
    settings.sources.greenhouse.company_boards = ["vercel", "stripe"]  # software-only, finance+software
    settings.sources.filter_boards_by_profile = True

    connectors = build_enabled_connectors(
        settings.sources,
        resume_summary={"titles": ["Payroll Specialist"], "industries": [], "skills": []},
        preferences=settings.preferences,
    )

    greenhouse = next(c for c in connectors if isinstance(c, GreenhouseConnector))
    assert greenhouse.company_boards == ["stripe"]


def test_build_enabled_connectors_skips_a_connector_entirely_if_every_board_is_filtered_out():
    settings = Settings()
    settings.sources.greenhouse.enabled = True
    settings.sources.greenhouse.company_boards = ["vercel"]  # software-only
    settings.sources.filter_boards_by_profile = True

    connectors = build_enabled_connectors(
        settings.sources,
        resume_summary={"titles": ["Payroll Specialist"], "industries": [], "skills": []},
        preferences=settings.preferences,
    )

    assert not any(isinstance(c, GreenhouseConnector) for c in connectors)


def test_build_enabled_connectors_never_filters_when_toggle_is_off():
    settings = Settings()
    settings.sources.greenhouse.enabled = True
    settings.sources.greenhouse.company_boards = ["vercel", "stripe"]
    settings.sources.filter_boards_by_profile = False

    connectors = build_enabled_connectors(
        settings.sources,
        resume_summary={"titles": ["Payroll Specialist"], "industries": [], "skills": []},
        preferences=settings.preferences,
    )

    greenhouse = next(c for c in connectors if isinstance(c, GreenhouseConnector))
    assert greenhouse.company_boards == ["vercel", "stripe"]


def test_parse_career_site_url_extracts_tenant_shard_site_and_locale():
    site = parse_career_site_url(
        "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite"
    )
    assert site is not None
    assert site.tenant == "nvidia"
    assert site.shard == "wd5"
    assert site.site == "NVIDIAExternalCareerSite"
    assert site.locale == "en-US"
    assert site.host == "nvidia.wd5.myworkdayjobs.com"


def test_parse_career_site_url_defaults_locale_when_absent():
    site = parse_career_site_url("https://acme.wd1.myworkdayjobs.com/AcmeCareers")
    assert site is not None
    assert site.site == "AcmeCareers"
    assert site.locale == "en-US"


def test_parse_career_site_url_returns_none_for_a_non_workday_host():
    assert parse_career_site_url("https://boards.greenhouse.io/acme") is None


def test_parse_career_site_url_returns_none_when_site_segment_is_missing():
    assert parse_career_site_url("https://nvidia.wd5.myworkdayjobs.com/en-US") is None


def test_parse_career_site_url_returns_none_for_garbage_input():
    assert parse_career_site_url("not a url at all") is None


@respx.mock
def test_workday_connector_parses_a_job_with_its_detail_description(monkeypatch):
    monkeypatch.setattr("hanarr.connectors.workday.REQUEST_DELAY_SECONDS", 0)
    respx.post("https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/AcmeCareers/jobs").mock(
        return_value=httpx.Response(
            200,
            json={
                "total": 1,
                "jobPostings": [
                    {
                        "title": "Backend Engineer",
                        "externalPath": "/job/US-Remote/Backend-Engineer_JR123",
                        "locationsText": "US, Remote",
                        "bulletFields": ["JR123"],
                    }
                ],
            },
        )
    )
    respx.get(
        "https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/AcmeCareers/job/US-Remote/Backend-Engineer_JR123"
    ).mock(
        return_value=httpx.Response(
            200,
            json={"jobPostingInfo": {"jobDescription": "<p>We build things.</p>"}},
        )
    )
    connector = WorkdayConnector(["https://acme.wd1.myworkdayjobs.com/AcmeCareers"])
    postings = connector.fetch()

    assert len(postings) == 1
    posting = postings[0]
    assert posting.external_id == "JR123"
    assert posting.company == "acme"
    assert posting.title == "Backend Engineer"
    assert posting.remote is True
    assert posting.description == "We build things."
    assert posting.url == (
        "https://acme.wd1.myworkdayjobs.com/en-US/AcmeCareers/job/US-Remote/Backend-Engineer_JR123"
    )
    assert posting.posted_at is None


@respx.mock
def test_workday_connector_still_includes_the_job_when_the_detail_request_fails(monkeypatch):
    monkeypatch.setattr("hanarr.connectors.workday.REQUEST_DELAY_SECONDS", 0)
    respx.post("https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/AcmeCareers/jobs").mock(
        return_value=httpx.Response(
            200,
            json={
                "total": 1,
                "jobPostings": [
                    {
                        "title": "Backend Engineer",
                        "externalPath": "/job/US-Remote/Backend-Engineer_JR123",
                        "locationsText": "US, Remote",
                        "bulletFields": ["JR123"],
                    }
                ],
            },
        )
    )
    respx.get(
        "https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/AcmeCareers/job/US-Remote/Backend-Engineer_JR123"
    ).mock(return_value=httpx.Response(500))

    connector = WorkdayConnector(["https://acme.wd1.myworkdayjobs.com/AcmeCareers"])
    postings = connector.fetch()

    assert len(postings) == 1
    assert postings[0].description == ""


@respx.mock
def test_workday_connector_skips_an_unparsable_career_site_url_without_crashing(monkeypatch):
    monkeypatch.setattr("hanarr.connectors.workday.REQUEST_DELAY_SECONDS", 0)
    connector = WorkdayConnector(["https://not-a-workday-url.example.com/careers"])
    assert connector.fetch() == []


@respx.mock
def test_workday_connector_skips_the_whole_site_without_crashing_on_a_failed_list_request(monkeypatch):
    monkeypatch.setattr("hanarr.connectors.workday.REQUEST_DELAY_SECONDS", 0)
    respx.post("https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/AcmeCareers/jobs").mock(
        return_value=httpx.Response(500)
    )
    connector = WorkdayConnector(["https://acme.wd1.myworkdayjobs.com/AcmeCareers"])
    assert connector.fetch() == []


@respx.mock
def test_workday_connector_stops_paginating_once_the_cap_is_reached(monkeypatch):
    """Regression test for the request-volume cap: without it, a company
    with thousands of open roles would make this connector fetch pages
    (and a detail request per job) indefinitely."""
    monkeypatch.setattr("hanarr.connectors.workday.REQUEST_DELAY_SECONDS", 0)
    monkeypatch.setattr("hanarr.connectors.workday.PAGE_SIZE", 1)
    monkeypatch.setattr("hanarr.connectors.workday.MAX_POSTINGS_PER_SITE", 2)

    def _list_response(request):
        import json

        offset = json.loads(request.content).get("offset", 0)
        return httpx.Response(
            200,
            json={
                "total": 1000,
                "jobPostings": [
                    {
                        "title": f"Role {offset}",
                        "externalPath": f"/job/Role-{offset}",
                        "locationsText": "US, Remote",
                        "bulletFields": [f"JR{offset}"],
                    }
                ],
            },
        )

    respx.post("https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/AcmeCareers/jobs").mock(
        side_effect=_list_response
    )
    respx.get(url__regex=r"https://acme\.wd1\.myworkdayjobs\.com/wday/cxs/acme/AcmeCareers/job/Role-\d+").mock(
        return_value=httpx.Response(200, json={"jobPostingInfo": {"jobDescription": "desc"}})
    )

    connector = WorkdayConnector(["https://acme.wd1.myworkdayjobs.com/AcmeCareers"])
    postings = connector.fetch()

    assert len(postings) == 2  # MAX_POSTINGS_PER_SITE / PAGE_SIZE pages, one job each


def test_build_enabled_connectors_includes_workday_when_configured():
    settings = Settings()
    settings.sources.workday.enabled = True
    settings.sources.workday.career_site_urls = ["https://acme.wd1.myworkdayjobs.com/AcmeCareers"]

    connectors = build_enabled_connectors(settings.sources)

    assert any(isinstance(c, WorkdayConnector) for c in connectors)


def test_build_enabled_connectors_skips_workday_without_career_site_urls():
    settings = Settings()
    settings.sources.workday.enabled = True
    settings.sources.workday.career_site_urls = []

    connectors = build_enabled_connectors(settings.sources)

    assert not any(isinstance(c, WorkdayConnector) for c in connectors)


def test_build_enabled_connectors_never_filters_boards_the_user_added_themselves():
    settings = Settings()
    settings.sources.greenhouse.enabled = True
    settings.sources.greenhouse.company_boards = ["vercel", "my-local-employer"]
    settings.sources.filter_boards_by_profile = True

    connectors = build_enabled_connectors(
        settings.sources,
        resume_summary={"titles": ["Payroll Specialist"], "industries": [], "skills": []},
        preferences=settings.preferences,
    )

    greenhouse = next(c for c in connectors if isinstance(c, GreenhouseConnector))
    assert greenhouse.company_boards == ["my-local-employer"]
