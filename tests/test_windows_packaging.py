from pathlib import Path
import json
import sys

import hanarr.packaged as packaged


ROOT = Path(__file__).parents[1]


def test_inno_setup_preserves_user_data_and_defines_shortcuts():
    source = (ROOT / "installer" / "hanarr.iss").read_text(encoding="utf-8")
    assert "PrivilegesRequired=lowest" in source
    assert "{autoprograms}" in source
    assert "{autodesktop}" in source
    assert "do not remove {localappdata}\\Hanarr" in source


def test_packaged_entry_points_use_explicit_launch_modes():
    source = (ROOT / "src" / "hanarr" / "packaged.py").read_text(encoding="utf-8")
    assert 'cli(["--config", str(config_path), "serve", "--launch-mode", mode]' in source
    assert 'run("browser")' in source
    assert 'run("webview")' in source


def test_installer_declares_app_mutex_and_closes_running_instances_for_updates():
    """auto-update (self_update.py) launches the new installer silently
    while the app is still running -- Setup needs AppMutex to detect that
    (matching the mutex packaged.py holds) and CloseApplications/
    RestartApplications to close and relaunch it as part of the install,
    rather than the update failing on locked files."""
    source = (ROOT / "installer" / "hanarr.iss").read_text(encoding="utf-8")
    assert "AppMutex=HanarrSingleInstanceMutex" in source
    assert "CloseApplications=yes" in source
    assert "RestartApplications=yes" in source


def test_packaged_run_holds_a_single_instance_mutex_matching_the_installer(monkeypatch):
    """The mutex name here must match installer/hanarr.iss's AppMutex
    exactly, or Setup can't detect the running app during an update."""
    iss_source = (ROOT / "installer" / "hanarr.iss").read_text(encoding="utf-8")
    assert "AppMutex=HanarrSingleInstanceMutex" in iss_source

    calls = []

    class FakeKernel32:
        def CreateMutexW(self, *args):
            calls.append(args)
            return 12345

    class FakeWindll:
        kernel32 = FakeKernel32()

    import ctypes

    monkeypatch.setattr(ctypes, "windll", FakeWindll(), raising=False)

    packaged._hold_single_instance_mutex()

    assert len(calls) == 1
    assert calls[0][-1] == "HanarrSingleInstanceMutex"


def test_hold_single_instance_mutex_does_not_raise_without_win32(monkeypatch):
    """Best-effort: must not crash startup if ctypes.windll isn't
    available (e.g. this somehow runs on a non-Windows OS)."""
    import ctypes

    monkeypatch.delattr(ctypes, "windll", raising=False)
    packaged._hold_single_instance_mutex()  # must not raise


def test_windows_build_script_builds_both_runtimes_before_inno():
    source = (ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")
    assert "Windows packaging preflight failed:" in source
    assert "$ValidateOnly" in source
    assert "PyInstaller is unavailable" in source
    assert "Inno Setup compiler" in source
    assert "--name HanarrBrowser" in source
    assert "--name HanarrDesktop" in source
    assert 'installer\\hanarr.iss' in source
    assert "expected installer artifact" in source
    assert "release-metadata.json" in source
    assert "Get-FileHash" in source
    assert "unsigned-success" in source


def test_release_metadata_allows_unsigned_release():
    metadata = json.loads((ROOT / "packaging" / "release-metadata.json").read_text(encoding="utf-8"))
    assert metadata["product"] == "Hanarr"
    assert metadata["platform"] == "windows"
    assert metadata["signing"]["required_for_release"] is False
    assert metadata["signing"]["status"] == "not-configured"


def test_release_metadata_version_matches_the_package_version():
    """publish-windows-release.yml refuses to publish unless the release
    tag, packaging/release-metadata.json, and pyproject.toml all agree on
    the version -- but that check only runs in CI, at tag-push time, which
    is too late to catch a forgotten bump cheaply. release-metadata.json
    was left at 0.1.28 for eleven releases (through 0.1.39) before this
    test existed, since nothing local ever exercised it."""
    import tomllib

    metadata = json.loads((ROOT / "packaging" / "release-metadata.json").read_text(encoding="utf-8"))
    package_version = tomllib.load((ROOT / "pyproject.toml").open("rb"))["project"]["version"]
    assert metadata["version"] == package_version


def test_windows_workflow_tests_preflights_builds_and_only_uploads_success():
    source = (ROOT / ".github" / "workflows" / "windows-installer.yml").read_text(encoding="utf-8")
    assert "python -m pytest -q" in source
    assert "python -m compileall -q src" in source
    assert "git diff --check" in source
    assert "build_windows.ps1 -ValidateOnly" in source
    assert "Inno Setup 6\\ISCC.exe" in source
    assert "build_windows.ps1 -InnoSetup" in source
    assert "actions/upload-artifact@v4" in source
    assert "if: ${{ success() }}" in source
    assert "installer/output/*.sha256" in source


def test_manual_release_workflow_builds_and_publishes_release():
    source = (ROOT / ".github" / "workflows" / "publish-windows-release.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in source
    assert "contents: write" in source
    assert "Release — Publish Windows Installer" in source
    assert "git describe --tags --exact-match HEAD" in source
    assert "packaging\\release-metadata.json" in source
    assert "pyproject.toml" in source
    assert "Publish GitHub Release" in source
    assert "--draft" not in source
    assert "--verify-tag" in source
    assert "Unsigned Windows installer." in source
    assert "Hanarr-Setup-$env:RELEASE_VERSION.sha256" in source


def test_installer_wires_both_launchers_and_preserves_user_data():
    source = (ROOT / "installer" / "hanarr.iss").read_text(encoding="utf-8")
    assert 'Source: "..\\dist\\HanarrDesktop.exe"' in source
    assert 'Source: "..\\dist\\HanarrBrowser.exe"' in source
    assert 'Filename: "{app}\\HanarrDesktop.exe"' in source
    assert 'Filename: "{app}\\HanarrBrowser.exe"' in source
    assert "[UninstallDelete]" in source
    assert "{localappdata}\\Hanarr" in source


def test_installer_upgrade_target_is_stable_and_uninstall_does_not_delete_user_data():
    source = (ROOT / "installer" / "hanarr.iss").read_text(encoding="utf-8")
    uninstall_section = source.split("[UninstallDelete]", 1)[1]
    assert "DefaultDirName={localappdata}\\Programs\\Hanarr" in source
    assert "AppId={{D4A3A6E5-7B52-4A6B-9B57-6C4F5E08D3B1}" in source
    assert "{localappdata}\\Hanarr" not in uninstall_section.replace(
        "; Deliberately do not remove {localappdata}\\Hanarr. It contains user data.", ""
    )


def test_ensure_standard_streams_replaces_none_streams():
    """Regression test for a packaged-runtime crash: a PyInstaller ``--windowed``
    build has no console, so ``sys.stdout``/``sys.stderr`` are ``None``. Uvicorn's
    default logging formatter calls ``sys.stdout.isatty()`` while configuring
    itself and crashes with ``AttributeError``/``ValueError`` before the server
    can start. ``_ensure_standard_streams`` must give both a real stream.
    """
    assert sys.stdout is not None
    assert sys.stderr is not None
    packaged._ensure_standard_streams()  # no-op with real streams present
    assert sys.stdout is not None
    assert sys.stderr is not None


def test_ensure_standard_streams_handles_none(monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    packaged._ensure_standard_streams()

    assert sys.stdout is not None
    assert sys.stderr is not None
    # Must not raise, matching what uvicorn's default formatter calls. The
    # actual isatty() result is platform-dependent (os.devnull reports True
    # on Windows); only absence of a crash is asserted here.
    sys.stdout.isatty()
    sys.stderr.isatty()


def test_packaged_runtime_preserves_existing_user_data_on_upgrade(tmp_path, monkeypatch):
    user_data = tmp_path / "Hanarr"
    user_data.mkdir()
    (user_data / "config.yaml").write_text("preferences:\n  min_salary: 123\n", encoding="utf-8")
    (user_data / "data").mkdir()
    (user_data / "data" / "hanarr.db").write_bytes(b"existing database")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "config.example.yaml").write_text("preferences: {}\n", encoding="utf-8")

    monkeypatch.setattr(packaged, "USER_DATA_DIR", user_data)
    monkeypatch.setattr(packaged, "_bundle_root", lambda: bundle)

    assert packaged.prepare_user_data() == user_data / "config.yaml"
    assert (user_data / "config.yaml").read_text(encoding="utf-8") == "preferences:\n  min_salary: 123\n"
    assert (user_data / "data" / "hanarr.db").read_bytes() == b"existing database"
