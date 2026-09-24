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
    assert "--name HanarrBrowser" in source
    assert "--name HanarrDesktop" in source
    assert 'installer\\hanarr.iss' in source
