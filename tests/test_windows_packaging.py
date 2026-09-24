from pathlib import Path


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


def test_installer_wires_both_launchers_and_preserves_user_data():
    source = (ROOT / "installer" / "hanarr.iss").read_text(encoding="utf-8")
    assert 'Source: "..\\dist\\HanarrDesktop.exe"' in source
    assert 'Source: "..\\dist\\HanarrBrowser.exe"' in source
    assert 'Filename: "{app}\\HanarrDesktop.exe"' in source
    assert 'Filename: "{app}\\HanarrBrowser.exe"' in source
    assert "[UninstallDelete]" in source
    assert "{localappdata}\\Hanarr" in source
