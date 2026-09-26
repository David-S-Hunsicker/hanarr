"""Opt-in release metadata checks with no download or installation side effects."""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Any

import httpx

from . import __version__
from .config import Settings

_VERSION_RE = re.compile(r"^v?([0-9]+)\.([0-9]+)\.([0-9]+)(?:[-+][0-9A-Za-z.-]+)?$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class UpdateCheckError(RuntimeError):
    """The configured release source was unreachable or invalid."""


@dataclass(frozen=True)
class UpdateAsset:
    name: str
    url: str
    sha256: str
    platform: str = ""


@dataclass(frozen=True)
class ReleaseMetadata:
    version: str
    release_notes_url: str
    notes: str
    assets: tuple[UpdateAsset, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "release_notes_url": self.release_notes_url,
            "notes": self.notes,
            "assets": [asdict(asset) for asset in self.assets],
        }


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = _VERSION_RE.fullmatch(value.strip())
    if not match:
        raise UpdateCheckError(f"Invalid release version {value!r}; expected semver like 1.2.3.")
    return tuple(int(part) for part in match.groups())


def parse_release_metadata(payload: dict[str, Any]) -> ReleaseMetadata:
    if not isinstance(payload, dict):
        raise UpdateCheckError("Release metadata must be a JSON object.")
    version = str(payload.get("version", "")).strip()
    _version_tuple(version)
    notes_url = str(payload.get("release_notes_url", "")).strip()
    if not notes_url.startswith(("https://", "http://")):
        raise UpdateCheckError("Release metadata must include an http(s) release_notes_url.")
    raw_assets = payload.get("assets", [])
    if not isinstance(raw_assets, list):
        raise UpdateCheckError("Release metadata assets must be a list.")
    assets: list[UpdateAsset] = []
    for raw in raw_assets:
        if not isinstance(raw, dict):
            raise UpdateCheckError("Each release asset must be an object.")
        name = str(raw.get("name", "")).strip()
        url = str(raw.get("url", "")).strip()
        checksum = str(raw.get("sha256", "")).strip().lower()
        if not name or not url.startswith(("https://", "http://")) or not _SHA256_RE.fullmatch(checksum):
            raise UpdateCheckError("Each release asset requires a name, http(s) URL, and SHA-256 checksum.")
        assets.append(UpdateAsset(name, url, checksum, str(raw.get("platform", "")).strip()))
    return ReleaseMetadata(version, notes_url, str(payload.get("notes", "")), tuple(assets))


def _github_payload(payload: dict[str, Any]) -> dict[str, Any]:
    tag = str(payload.get("tag_name", "")).strip()
    assets = []
    for asset in payload.get("assets", []):
        if not isinstance(asset, dict):
            continue
        # GitHub exposes a digest on newer releases; custom release metadata
        # remains the preferred source when a checksum is not published.
        digest = str(asset.get("digest", "")).strip()
        if digest.startswith("sha256:"):
            digest = digest[7:]
        assets.append(
            {
                "name": asset.get("name", ""),
                "url": asset.get("browser_download_url", ""),
                "sha256": digest,
                "platform": "",
            }
        )
    return {
        "version": tag,
        "release_notes_url": payload.get("html_url", ""),
        "notes": payload.get("body", ""),
        "assets": assets,
    }


def _source(settings: Settings) -> tuple[str, dict[str, str]]:
    if settings.updates.endpoint:
        return settings.updates.endpoint, {"Accept": "application/json"}
    if settings.updates.github_repository:
        return (
            f"https://api.github.com/repos/{settings.updates.github_repository}/releases/latest",
            {"Accept": "application/vnd.github+json"},
        )
    raise UpdateCheckError("No release endpoint or GitHub repository is configured.")


def check_for_update(settings: Settings, client: httpx.Client | None = None) -> dict[str, Any]:
    if not settings.updates.enabled:
        return {"status": "disabled", "current_version": __version__}
    url, headers = _source(settings)
    owns_client = client is None
    client = client or httpx.Client(timeout=settings.updates.timeout_seconds, follow_redirects=True)
    try:
        response = client.get(url, headers=headers)
        response.raise_for_status()
        payload = response.json()
        if settings.updates.github_repository and not settings.updates.endpoint:
            payload = _github_payload(payload)
        release = parse_release_metadata(payload)
    except (httpx.HTTPError, OSError, ValueError, UpdateCheckError) as exc:
        if isinstance(exc, UpdateCheckError):
            raise
        raise UpdateCheckError(f"Could not check releases: {exc}") from exc
    finally:
        if owns_client:
            client.close()
    current = _version_tuple(__version__)
    latest = _version_tuple(release.version)
    return {
        "status": "update_available" if latest > current else "up_to_date",
        "current_version": __version__,
        "release": release.to_dict(),
    }


def verify_sha256(path: str, expected: str) -> bool:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().lower() == expected.lower()
