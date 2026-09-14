import httpx
import respx

from jobcopilot.connectors.arbeitnow import ArbeitnowConnector
from jobcopilot.connectors.greenhouse import GreenhouseConnector
from jobcopilot.connectors.remoteok import RemoteOKConnector


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
