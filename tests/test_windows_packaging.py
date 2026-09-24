from pathlib import Path
import json


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
