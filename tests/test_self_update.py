import hashlib
from pathlib import Path

import pytest

from hanarr.self_update import (
    SelfUpdateError,
    apply_update,
    download_and_verify_installer,
    find_windows_installer_asset,
    is_packaged_build,
)
from hanarr.update_service import ReleaseMetadata, UpdateAsset


def _release(assets):
    return ReleaseMetadata(version="0.1.2", release_notes_url="https://example.test/notes", notes="", assets=tuple(assets))


def test_find_windows_installer_asset_picks_the_exe_not_sidecars():
    release = _release([
        UpdateAsset(name="Hanarr-Setup-0.1.2.sha256", url="https://x/checksum", sha256=""),
        UpdateAsset(name="release-metadata.json", url="https://x/meta", sha256=""),
        UpdateAsset(name="Hanarr-Setup-0.1.2.exe", url="https://x/installer", sha256="a" * 64),
    ])
    asset = find_windows_installer_asset(release)
    assert asset is not None
    assert asset.name == "Hanarr-Setup-0.1.2.exe"


def test_find_windows_installer_asset_returns_none_when_no_exe_present():
    release = _release([UpdateAsset(name="release-metadata.json", url="https://x/meta", sha256="")])
    assert find_windows_installer_asset(release) is None


def test_download_and_verify_installer_succeeds_with_a_matching_checksum(tmp_path):
    content = b"pretend installer bytes"
    digest = hashlib.sha256(content).hexdigest()

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def raise_for_status(self):
            return None

        def iter_bytes(self, chunk_size):
            yield content

    class Client:
        def stream(self, *args, **kwargs):
            return Response()

    asset = UpdateAsset(name="Hanarr-Setup-0.1.2.exe", url="https://x/installer", sha256=digest)
    destination = tmp_path / "Hanarr-Setup-0.1.2.exe"

    result = download_and_verify_installer(asset, destination, client=Client())

    assert result == destination
    assert destination.read_bytes() == content


def test_download_and_verify_installer_rejects_a_checksum_mismatch(tmp_path):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def raise_for_status(self):
            return None

        def iter_bytes(self, chunk_size):
            yield b"tampered or corrupted bytes"

    class Client:
        def stream(self, *args, **kwargs):
            return Response()

    asset = UpdateAsset(name="Hanarr-Setup-0.1.2.exe", url="https://x/installer", sha256="f" * 64)
    destination = tmp_path / "Hanarr-Setup-0.1.2.exe"

    with pytest.raises(SelfUpdateError, match="checksum"):
        download_and_verify_installer(asset, destination, client=Client())

    assert not destination.exists()


def test_download_and_verify_installer_cleans_up_on_download_failure(tmp_path):
    import httpx

    class Client:
        def stream(self, *args, **kwargs):
            raise httpx.ConnectError("offline")

    asset = UpdateAsset(name="Hanarr-Setup-0.1.2.exe", url="https://x/installer", sha256="a" * 64)
    destination = tmp_path / "Hanarr-Setup-0.1.2.exe"

    with pytest.raises(SelfUpdateError):
        download_and_verify_installer(asset, destination, client=Client())

    assert not destination.exists()
    assert not destination.with_name(f".{destination.name}.part").exists()


def test_apply_update_refuses_outside_a_packaged_build(tmp_path):
    """A source checkout has no installed .exe for an installer to
    replace -- apply_update() must refuse rather than silently no-op or,
    worse, actually run an installer against a dev environment."""
    assert is_packaged_build() is False  # true for the test process itself
    with pytest.raises(SelfUpdateError, match="packaged"):
        apply_update(tmp_path / "Hanarr-Setup-0.1.2.exe")


def test_apply_update_refuses_when_the_staged_installer_is_missing(tmp_path, monkeypatch):
    import hanarr.self_update as self_update_mod

    monkeypatch.setattr(self_update_mod, "is_packaged_build", lambda: True)
    with pytest.raises(SelfUpdateError, match="not found"):
        apply_update(tmp_path / "does-not-exist.exe")
