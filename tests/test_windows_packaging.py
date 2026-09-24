from pathlib import Path
import json
import sys

import jobcopilot.packaged as packaged


ROOT = Path(__file__).parents[1]


def test_inno_setup_preserves_user_data_and_defines_shortcuts():
    source = (ROOT / "installer" / "hanarr.iss").read_text(encoding="utf-8")
    assert "PrivilegesRequired=lowest" in source
    assert "{autoprograms}" in source
    assert "{autodesktop}" in source
    assert "do not remove {localappdata}\\Hanarr" in source


def test_packaged_entry_points_use_explicit_launch_modes():
    source = (ROOT / "src" / "jobcopilot" / "packaged.py").read_text(encoding="utf-8")
    assert 'cli(["--config", str(config_path), "serve", "--launch-mode", mode]' in source
    assert 'run("browser")' in source
    assert 'run("webview")' in source


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


def test_release_metadata_requires_signing_before_release():
    metadata = json.loads((ROOT / "packaging" / "release-metadata.json").read_text(encoding="utf-8"))
    assert metadata["product"] == "Hanarr"
    assert metadata["platform"] == "windows"
    assert metadata["signing"]["required_for_release"] is True
    assert metadata["signing"]["status"] == "not-configured"


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
    (user_data / "data" / "jobcopilot.db").write_bytes(b"existing database")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "config.example.yaml").write_text("preferences: {}\n", encoding="utf-8")

    monkeypatch.setattr(packaged, "USER_DATA_DIR", user_data)
    monkeypatch.setattr(packaged, "_bundle_root", lambda: bundle)

    assert packaged.prepare_user_data() == user_data / "config.yaml"
    assert (user_data / "config.yaml").read_text(encoding="utf-8") == "preferences:\n  min_salary: 123\n"
    assert (user_data / "data" / "jobcopilot.db").read_bytes() == b"existing database"
