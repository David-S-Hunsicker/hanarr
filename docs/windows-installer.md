# Windows installer

The supported packaging path is PyInstaller for the runtime and Inno Setup for
the conventional Windows installer. The installer is per-user (`PrivilegesRequired=lowest`)
and installs replaceable binaries under `%LOCALAPPDATA%\Programs\Hanarr`.

The packaged launchers use the same `hanarr serve` backend:

- **Hanarr** starts the local dashboard in the optional desktop webview.
- **Hanarr (Browser)** starts the local dashboard in the default browser.

Configuration, resumes, SQLite data, migrations, and setup state are kept in
`%LOCALAPPDATA%\Hanarr`, outside the application directory. Uninstall removes
the binaries, shortcuts, and Add/Remove Programs registration but preserves
that data. Removing user data is intentionally a separate manual action.
The installer never installs, starts, or silently replaces Ollama; the existing
explicit-consent setup flow remains responsible for any optional provider action.

## Build

On Windows, install [Inno Setup 6](https://jrsoftware.org/isinfo.php), ensure
`ISCC.exe` is on `PATH`, and run from a clean checkout:

```powershell
# Deterministic tool/input check; does not build or change the checkout.
.\scripts\build_windows.ps1 -ValidateOnly

# Build the unsigned local installer.
.\scripts\build_windows.ps1
```

`-ValidateOnly` fails before any install/build step when Python, PyInstaller, Inno
Setup, or a required packaging input is missing. The full script installs the optional
`packaging` dependencies, creates both
PyInstaller executables, then invokes `installer\hanarr.iss`. It writes an
**unsigned** `installer\output\Hanarr-Setup-0.1.9.exe`, a SHA-256 sidecar, and output metadata;
this repository does
not claim that artifact is signed or released. A successful compiler exit is not sufficient:
the script also checks that the expected non-empty installer artifact exists.

The checked-in version is recorded in both `packaging/release-metadata.json` and
`pyproject.toml`; keep them aligned. **CI — Test and Build Windows Installer** in
`.github/workflows/windows-installer.yml` installs the test and packaging dependencies plus Inno
Setup, runs the full tests and `-ValidateOnly` preflight, and uploads the installer only after the
build and artifact checks succeed. Missing tools fail the job; they never produce a
success-shaped artifact. The uploaded artifact is explicitly unsigned and is not a release.

To publish a release, create and push a stable version tag (for example, `v0.1.0`) on a commit
where both version fields match. From GitHub Actions, manually run **Release — Publish Windows
Installer** against the default branch and enter that existing tag. The workflow checks out the
tag, verifies the tag and both version fields match, runs the tests and packaging checks, builds
the installer, and publishes a GitHub Release containing the unsigned installer, SHA-256 file,
metadata, generated notes, and an unsigned-installer warning. The manual workflow run is the
publication approval; there is no draft-review step. The workflow does not sign artifacts, and
Windows may show an unknown-publisher or SmartScreen warning. No signing secret is required.

## Release-readiness validation

Before publishing the first public release, perform and record a clean Windows machine or VM test
of installation, launch, upgrade, and uninstall/data preservation. The workflow publishes
automatically after the manually requested tagged build passes; it does not perform this manual
test. Download and inspect the published assets before announcing the first release. Retain the
exact release tag/commit and artifact SHA-256 for the release record.

On a clean Windows machine or VM with no Python, terminal tooling, or pre-existing
Hanarr installation:

1. Install the unsigned installer as a normal user and verify both Start Menu shortcuts.
2. Select the optional Desktop shortcut and verify it launches the same backend in
   webview mode; verify the Browser shortcut opens the dashboard in the default browser.
3. Create representative configuration, resume, and SQLite data under
   `%LOCALAPPDATA%\Hanarr`, then uninstall and confirm those files remain while the
   application directory, shortcuts, and Add/Remove Programs entry are removed.
4. Install the next version over the first installation and verify data, configuration,
   migrations, launch modes, and both shortcuts remain usable.
5. Repeat with an existing Ollama installation, no network, cancelled/failed provider
   setup, and a migration failure. Confirm each failure is explicit and recoverable;
   the installer itself must never install, start, or silently replace Ollama.
