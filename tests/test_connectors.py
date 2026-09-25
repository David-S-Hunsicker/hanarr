import datetime as dt

import httpx
import respx

from hanarr.connectors.arbeitnow import ArbeitnowConnector
from hanarr.connectors.base import to_naive_utc
from hanarr.connectors.greenhouse import GreenhouseConnector
from hanarr.connectors.lever import LeverConnector
from hanarr.connectors.remoteok import RemoteOKConnector


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
