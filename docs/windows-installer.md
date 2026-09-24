# Windows installer

The supported packaging path is PyInstaller for the runtime and Inno Setup for
the conventional Windows installer. The installer is per-user (`PrivilegesRequired=lowest`)
and installs replaceable binaries under `%LOCALAPPDATA%\Programs\Hanarr`.

The packaged launchers use the same `jobcopilot serve` backend:

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
**unsigned** `installer\output\Hanarr-Setup-0.1.0.exe`, a SHA-256 sidecar, and output metadata;
this repository does
not claim that artifact is signed or released. Release work still requires a
real Windows clean-machine test, certificate-backed Authenticode signing,
signature verification, version automation, and publishing. A successful compiler
exit is not sufficient: the script also checks that the expected non-empty installer
artifact exists.

The checked-in version and release-gate contract is `packaging/release-metadata.json`.
The repository workflow in `.github/workflows/windows-installer.yml` installs the test and
packaging dependencies plus Inno Setup, runs the full tests and `-ValidateOnly` preflight, and
uploads the installer only after the build and artifact checks succeed. Missing tools fail the
job; they never produce a success-shaped artifact. The uploaded artifact is explicitly unsigned
and is not a release.

## Release-readiness validation

Before release, retain the exact commit, Python/PyInstaller/Inno versions, and SHA-256
of the unsigned and signed artifacts. Sign the installer and both packaged executables
with the release certificate using the organization's approved Authenticode process,
then verify each signature and timestamp with `Get-AuthenticodeSignature`; do not
describe an unsigned artifact as released.

The `signing.required_for_release` metadata gate must be satisfied before any release
publication. The current workflow intentionally has no certificate or notarization secret
configured and only uploads a short-retention CI artifact for inspection; adding signing and
release publication is a separate, explicitly approved milestone.

On a clean Windows machine or VM with no Python, terminal tooling, or pre-existing
Hanarr installation:

1. Install the signed installer as a normal user and verify both Start Menu shortcuts.
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
