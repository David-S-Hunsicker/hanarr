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
.\scripts\build_windows.ps1
```

The script installs the optional `packaging` dependencies, creates both
PyInstaller executables, then invokes `installer\hanarr.iss`. It writes an
**unsigned** `installer\output\Hanarr-Setup-0.1.0.exe`; this repository does
not claim that artifact is signed or released. Release work still requires a
real Windows clean-machine test, certificate-backed Authenticode signing,
signature verification, version automation, and publishing.
