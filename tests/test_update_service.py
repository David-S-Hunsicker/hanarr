import httpx
import pytest
import respx

from hanarr.config import Settings
from hanarr.update_service import UpdateCheckError, check_for_update, parse_release_metadata


def _settings(tmp_path, **updates):
    settings = Settings()
    settings.data_dir = tmp_path / "data"
    settings.data_dir.mkdir()
    settings.updates.enabled = True
    settings.updates.endpoint = "https://updates.example.test/release.json"
    for key, value in updates.items():
        setattr(settings.updates, key, value)
    return settings


def test_update_check_validates_metadata_and_reports_new_release(tmp_path):
    settings = _settings(tmp_path)
    with respx.mock:
        route = respx.get(settings.updates.endpoint).mock(
            return_value=httpx.Response(
                200,
                json={
                    "version": "0.2.0",
                    "release_notes_url": "https://example.test/releases/0.2.0",
                    "notes": "Bug fixes",
                    "assets": [
                        {
                            "name": "Hanarr-Setup.exe",
                            "url": "https://example.test/Hanarr-Setup.exe",
                            "sha256": "a" * 64,
                            "platform": "windows",
                        }
                    ],
                },
            )
        )
        result = check_for_update(settings)

    assert route.called
    assert result["status"] == "update_available"
    assert result["release"]["assets"][0]["sha256"] == "a" * 64


@pytest.mark.parametrize(
    "payload",
    [
        {"version": "not-semver", "release_notes_url": "https://example.test"},
        {"version": "1.0.0", "release_notes_url": "javascript:bad"},
        {
            "version": "1.0.0",
            "release_notes_url": "https://example.test",
            "assets": [{"name": "setup.exe", "url": "https://example.test/setup.exe", "sha256": "bad"}],
        },
    ],
)
def test_malformed_metadata_is_rejected(payload):
    with pytest.raises(UpdateCheckError):
        parse_release_metadata(payload)


def test_offline_update_check_fails_clearly(tmp_path):
    settings = _settings(tmp_path)
    with respx.mock:
        respx.get(settings.updates.endpoint).mock(side_effect=httpx.ConnectError("offline"))
        with pytest.raises(UpdateCheckError, match="Could not check releases"):
            check_for_update(settings)


def test_update_checks_are_opt_in(tmp_path):
    settings = _settings(tmp_path)
    settings.updates.enabled = False
    assert check_for_update(settings) == {"status": "disabled", "current_version": "0.1.0"}


def test_github_release_metadata_is_supported(tmp_path):
    settings = _settings(
        tmp_path,
        endpoint="",
        github_repository="David-S-Hunsicker/hanarr",
    )
    url = "https://api.github.com/repos/David-S-Hunsicker/hanarr/releases/latest"
    with respx.mock:
        respx.get(url).mock(
            return_value=httpx.Response(
                200,
                json={
                    "tag_name": "v0.2.0",
                    "html_url": "https://github.com/David-S-Hunsicker/hanarr/releases/tag/v0.2.0",
                    "body": "Notes",
                    "assets": [
                        {
                            "name": "setup.exe",
                            "browser_download_url": "https://github.com/David-S-Hunsicker/hanarr/releases/download/v0.2.0/setup.exe",
                            "digest": "sha256:" + "b" * 64,
                        }
                    ],
                },
            )
        )
        result = check_for_update(settings)
    assert result["status"] == "update_available"

