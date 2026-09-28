"""Downloads, verifies, and applies a Hanarr update -- the actual
download/apply half of what update_service.py only checks metadata for.

Splitting this from update_service.py deliberately: that module's own
docstring says "no download or installation side effects" and its tests
depend on that being true. This module is where those side effects
actually live, gated by settings.updates.auto_update rather than being
unconditional.

Safety properties:
- The downloaded installer's SHA-256 is verified against the checksum in
  the release metadata (already validated as a real 64-hex-char string by
  update_service.parse_release_metadata) before it's ever run. A mismatch
  is treated the same as any other failure -- the file is deleted, nothing
  is applied.
- apply_update() only ever runs when this process is a packaged build
  (see is_packaged_build()) -- there's no installed .exe to silently
  replace when running from a source checkout, so it's a deliberate no-op
  there rather than trying to self-update a dev environment.
- The installer is launched silently (/VERYSILENT /SUPPRESSMSGBOXES) but
  this process does not try to pre-emptively kill or replace itself --
  installer/hanarr.iss declares AppMutex + CloseApplications so Inno Setup
  itself detects the running app (via a named mutex created at startup,
  see packaged.py) and closes/relaunches it as part of the install. That's
  the standard, well-tested mechanism for this, instead of a hand-rolled
  "kill our own process at the right moment" race.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path
from typing import Iterator

import httpx

from .update_service import ReleaseMetadata, UpdateAsset

# A packaged Hanarr installer is currently in the low hundreds of MB
# (PyInstaller-bundled Python runtime + dependencies) -- generous headroom
# above that, same reasoning as OLLAMA_INSTALLER_MAX_BYTES: a safety cap
# against a misbehaving/redirected response streaming forever, not a tight
# estimate.
UPDATE_INSTALLER_MAX_BYTES = 1 * 1024 * 1024 * 1024


class SelfUpdateError(RuntimeError):
    """An update could not be safely downloaded, verified, or applied."""


def is_packaged_build() -> bool:
    """True only inside a PyInstaller-frozen executable. A source checkout
    has no installed .exe for an installer to replace, so apply_update()
    must not try."""
    return hasattr(sys, "_MEIPASS")


def find_windows_installer_asset(release: ReleaseMetadata) -> UpdateAsset | None:
    """Picks the installer .exe out of a release's assets, skipping the
    .sha256/.json sidecars published alongside it (see
    publish-windows-release.yml)."""
    for asset in release.assets:
        if asset.name.lower().endswith(".exe"):
            return asset
    return None


def release_from_check_result(release_dict: dict) -> ReleaseMetadata:
    """Rebuilds a real ReleaseMetadata from check_for_update()'s
    JSON-friendly dict (ReleaseMetadata.to_dict()) -- shared by the
    scheduler's background check job and the dashboard's manual
    "Install now" route so both stage/apply updates the same way."""
    return ReleaseMetadata(
        version=release_dict["version"],
        release_notes_url=release_dict.get("release_notes_url", ""),
        notes=release_dict.get("notes", ""),
        assets=tuple(UpdateAsset(**a) for a in release_dict.get("assets", [])),
    )


def _write_bounded(chunks: Iterator[bytes], destination: Path, max_bytes: int) -> int:
    written = 0
    try:
        with destination.open("wb") as output:
            for chunk in chunks:
                written += len(chunk)
                if written > max_bytes:
                    raise SelfUpdateError(f"Update download exceeded the {max_bytes} byte safety limit.")
                output.write(chunk)
    except SelfUpdateError:
        destination.unlink(missing_ok=True)
        raise
    except (OSError, httpx.HTTPError) as exc:
        destination.unlink(missing_ok=True)
        raise SelfUpdateError(f"Update download failed: {exc}") from exc
    return written


def download_and_verify_installer(
    asset: UpdateAsset, destination: Path | str, *, client: httpx.Client | None = None,
) -> Path:
    """Downloads `asset` to `destination` and verifies its SHA-256 against
    asset.sha256 before returning. Raises SelfUpdateError (and deletes the
    file) on any download failure, size-limit breach, or checksum
    mismatch -- a corrupted or tampered download must never be applied."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staged = destination.with_name(f".{destination.name}.part")

    http_client = client or httpx.Client(timeout=30.0, follow_redirects=True)
    close_client = client is None
    try:
        try:
            with http_client.stream("GET", asset.url) as response:
                response.raise_for_status()
                _write_bounded(response.iter_bytes(64 * 1024), staged, UPDATE_INSTALLER_MAX_BYTES)
        except SelfUpdateError:
            raise
        except (OSError, httpx.HTTPError) as exc:
            staged.unlink(missing_ok=True)
            raise SelfUpdateError(f"Update download failed: {exc}") from exc
    finally:
        if close_client:
            http_client.close()

    digest = hashlib.sha256(staged.read_bytes()).hexdigest()
    if digest.lower() != asset.sha256.lower():
        staged.unlink(missing_ok=True)
        raise SelfUpdateError(
            f"Downloaded installer's checksum ({digest}) does not match the published "
            f"checksum ({asset.sha256}) -- the file was not applied."
        )

    staged.replace(destination)
    return destination


def apply_update(installer_path: Path | str) -> None:
    """Launches the verified installer silently and detached. Does nothing
    to this process afterward -- Inno Setup's own CloseApplications /
    RestartApplications (see installer/hanarr.iss) handles closing and
    relaunching the running app as part of the install, not this code.

    No-ops (raises SelfUpdateError) outside a packaged build -- there is
    no installed executable for a source checkout to replace."""
    if not is_packaged_build():
        raise SelfUpdateError("apply_update() only runs inside a packaged Hanarr build.")
    installer_path = Path(installer_path)
    if not installer_path.exists():
        raise SelfUpdateError(f"Staged installer not found at {installer_path}.")
    subprocess.Popen(
        [str(installer_path), "/VERYSILENT", "/SUPPRESSMSGBOXES"],
        close_fds=True,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    )
