from pathlib import Path

import httpx
import pytest

from hanarr.ollama_setup import (
    HardwareInfo,
    SetupCancelled,
    SetupError,
    _executable_details,
    _prefer_ipv4_loopback,
    detect_ollama,
    installer_offer,
    pull_model,
    recommend_model,
    stage_ollama_installer,
)


@pytest.mark.parametrize(
    "url, expected",
    [
        ("http://localhost:11434", "http://127.0.0.1:11434"),
        ("http://localhost:11434/api/tags", "http://127.0.0.1:11434/api/tags"),
        ("http://localhost", "http://127.0.0.1"),
        ("https://example.com:8080/x", "https://example.com:8080/x"),
        ("http://127.0.0.1:11434", "http://127.0.0.1:11434"),
    ],
)
def test_prefer_ipv4_loopback_only_rewrites_localhost(url, expected):
    """Regression test: Windows resolves "localhost" to ::1 before 127.0.0.1,
    and Ollama's Windows service only binds IPv4, so a request to
    "localhost" can take several seconds to fall back to the working
    address -- long enough to blow past detect_ollama's short timeout even
    though the service is actually up. Only "localhost" is rewritten; any
    other host (including an already-IPv4 address) passes through untouched."""
    assert _prefer_ipv4_loopback(url) == expected


def test_executable_details_suppresses_a_console_window(monkeypatch):
    """Regression test: a user reported a console window briefly flashing
    on every tab/page switch in the packaged (windowed, console-less)
    desktop app. Root cause: detect_ollama() runs on nearly every
    dashboard render and calls this to get `ollama --version`, spawning a
    console-subsystem executable -- without suppressing console-window
    creation, Windows opens a fresh visible console for it every time."""
    captured = {}

    class Result:
        stdout = "ollama version 0.1.0"
        stderr = ""

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        return Result()

    monkeypatch.setattr("hanarr.ollama_setup.subprocess.run", fake_run)

    _executable_details(r"C:\Ollama\ollama.exe")

    assert captured.get("creationflags", 0) != 0


def test_model_recommendation_is_conservative_and_explained():
    recommendation = recommend_model(HardwareInfo(8, 20, "Windows"))

    assert recommendation.model == "qwen2.5:7b"
    assert "8–16" in recommendation.reason
    assert recommendation.confidence == "medium"


def test_low_storage_prefers_small_model():
    recommendation = recommend_model(HardwareInfo(32, 5, "Windows"))

    assert recommendation.model == "qwen2.5:3b"
    assert "storage" in recommendation.reason.lower()


def test_detect_ollama_reports_service_models_and_missing_configured_model(monkeypatch, tmp_path: Path):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"models": [{"name": "qwen2.5:7b", "size": 2 * 1024**3}]}

    monkeypatch.setattr("hanarr.ollama_setup.shutil.which", lambda _: r"C:\Ollama\ollama.exe")
    monkeypatch.setattr("hanarr.ollama_setup._executable_details", lambda _: "ollama version 0.1")
    monkeypatch.setattr("hanarr.ollama_setup.httpx.get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(
        "hanarr.ollama_setup.detect_hardware",
        lambda _: HardwareInfo(16, 50, "Windows"),
    )

    diagnostics = detect_ollama("qwen2.5:14b", data_path=tmp_path)

    assert diagnostics.executable_path.endswith("ollama.exe")
    assert diagnostics.service_reachable is True
    assert diagnostics.configured_model_available is False
    assert diagnostics.installed_models[0].name == "qwen2.5:7b"
    assert diagnostics.to_dict()["recommendation"]["model"] == "qwen2.5:14b"


def test_detect_ollama_checks_ipv4_loopback_not_localhost(monkeypatch, tmp_path: Path):
    requested_urls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"models": []}

    def fake_get(url, timeout=None):
        requested_urls.append(url)
        return Response()

    monkeypatch.setattr("hanarr.ollama_setup.shutil.which", lambda _: r"C:\Ollama\ollama.exe")
    monkeypatch.setattr("hanarr.ollama_setup.httpx.get", fake_get)
    monkeypatch.setattr(
        "hanarr.ollama_setup.detect_hardware",
        lambda _: HardwareInfo(16, 50, "Windows"),
    )

    detect_ollama("qwen2.5:14b", base_url="http://localhost:11434", data_path=tmp_path)

    assert requested_urls == ["http://127.0.0.1:11434/api/tags"]


def test_detect_ollama_falls_back_to_default_windows_install_path(monkeypatch, tmp_path: Path):
    """Regression test: a process started before Ollama's installer updated
    the user PATH (the common case right after installing, before a
    terminal restart) sees shutil.which("ollama") return None even though
    Ollama is genuinely installed and running. Fall back to the Windows
    installer's known per-user default location."""
    fake_ollama = tmp_path / "Programs" / "Ollama" / "ollama.exe"
    fake_ollama.parent.mkdir(parents=True)
    fake_ollama.write_bytes(b"")

    monkeypatch.setattr("hanarr.ollama_setup.shutil.which", lambda _: None)
    monkeypatch.setattr("hanarr.ollama_setup.platform.system", lambda: "Windows")
    monkeypatch.setattr("hanarr.ollama_setup.WINDOWS_DEFAULT_OLLAMA_PATH", fake_ollama)
    monkeypatch.setattr("hanarr.ollama_setup.httpx.get", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("offline")))
    monkeypatch.setattr("hanarr.ollama_setup.detect_hardware", lambda _: HardwareInfo(None, None, "unknown"))

    diagnostics = detect_ollama("qwen2.5:7b", data_path=tmp_path)

    assert diagnostics.executable_path == str(fake_ollama)


def test_detect_ollama_is_usable_when_service_is_unavailable(monkeypatch, tmp_path: Path):
    def fail(*args, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr("hanarr.ollama_setup.shutil.which", lambda _: None)
    # Deterministic regardless of whether Ollama actually happens to be
    # installed at its default location on the machine running this test.
    monkeypatch.setattr("hanarr.ollama_setup.WINDOWS_DEFAULT_OLLAMA_PATH", tmp_path / "not-installed" / "ollama.exe")
    monkeypatch.setattr("hanarr.ollama_setup.httpx.get", fail)
    monkeypatch.setattr(
        "hanarr.ollama_setup.detect_hardware",
        lambda _: HardwareInfo(None, None, "unknown"),
    )

    diagnostics = detect_ollama("qwen2.5:7b", data_path=tmp_path)

    assert diagnostics.executable_path is None
    assert diagnostics.service_reachable is False
    assert diagnostics.service_error == "offline"
    assert diagnostics.configured_model_available is False


def test_detect_ollama_is_usable_when_the_request_raises_a_plain_oserror(monkeypatch, tmp_path: Path):
    """Regression test: on machines running TLS-inspecting security software,
    ssl.create_default_context() (invoked by httpx even for plain http://
    requests, and patched by truststore) can raise PermissionError -- an
    OSError, not an httpx.HTTPError -- while honoring SSLKEYLOGFILE. This
    must degrade to an unreachable-service diagnosis, not crash the caller
    (which previously surfaced as an uncaught 500 on the dashboard)."""

    def fail(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("hanarr.ollama_setup.shutil.which", lambda _: None)
    monkeypatch.setattr("hanarr.ollama_setup.WINDOWS_DEFAULT_OLLAMA_PATH", tmp_path / "not-installed" / "ollama.exe")
    monkeypatch.setattr("hanarr.ollama_setup.httpx.get", fail)
    monkeypatch.setattr(
        "hanarr.ollama_setup.detect_hardware",
        lambda _: HardwareInfo(None, None, "unknown"),
    )

    diagnostics = detect_ollama("qwen2.5:7b", data_path=tmp_path)

    assert diagnostics.service_reachable is False
    assert "Permission denied" in diagnostics.service_error
    assert diagnostics.configured_model_available is False


def test_installer_download_requires_consent_and_does_not_create_a_file(tmp_path: Path):
    destination = tmp_path / "setup" / "OllamaSetup.exe"

    with pytest.raises(SetupError, match="consent"):
        stage_ollama_installer(destination, consent=False)

    assert not destination.exists()


def test_failed_installer_download_cleans_up_staging_file(tmp_path: Path):
    class Client:
        def stream(self, *args, **kwargs):
            raise httpx.ConnectError("offline")

    destination = tmp_path / "OllamaSetup.exe"
    with pytest.raises(SetupError, match="failed"):
        stage_ollama_installer(destination, consent=True, client=Client())

    assert not destination.exists()
    assert not destination.with_name(".OllamaSetup.exe.part").exists()


def test_interrupted_installer_download_cleans_up_staging_file(tmp_path: Path):
    from threading import Event

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def raise_for_status(self):
            return None

        def iter_bytes(self, _chunk_size):
            yield b"partial"

    class Client:
        def stream(self, *args, **kwargs):
            return Response()

    cancelled = Event()
    cancelled.set()
    destination = tmp_path / "OllamaSetup.exe"
    with pytest.raises(SetupCancelled):
        stage_ollama_installer(destination, consent=True, client=Client(), cancel_event=cancelled)

    assert not destination.exists()
    assert not destination.with_name(".OllamaSetup.exe.part").exists()


def test_installer_offer_shows_the_updated_size_estimate(tmp_path: Path):
    """The displayed size must reflect the raised limit -- the old "up to
    512 MB" text was itself wrong once the real installer grew past it."""
    offer = installer_offer(tmp_path / "OllamaSetup.exe")
    assert "512" not in offer.size
    assert "1.5 GB" in offer.size


def test_installer_byte_limit_exceeds_the_real_installer_size():
    """Regression test: the real Ollama Windows installer bundles CUDA/ROCm
    runtime libraries and is ~1.5 GB as of writing -- a user hit "Download
    exceeded the byte safety limit" against the old 512 MB cap, which was
    sized for a much smaller installer from long ago."""
    from hanarr.ollama_setup import OLLAMA_INSTALLER_MAX_BYTES

    realistic_size = int(1.5 * 1024 * 1024 * 1024)
    assert OLLAMA_INSTALLER_MAX_BYTES > realistic_size


def test_installer_download_succeeds_just_under_the_byte_limit(tmp_path: Path, monkeypatch):
    """Behavioral check of the same cap, at a small scale for test speed."""
    import hanarr.ollama_setup as ollama_setup_mod

    monkeypatch.setattr(ollama_setup_mod, "OLLAMA_INSTALLER_MAX_BYTES", 1024)

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def raise_for_status(self):
            return None

        def iter_bytes(self, chunk_size):
            yield b"x" * 1000  # under the 1024-byte patched limit

    class Client:
        def stream(self, *args, **kwargs):
            return Response()

    destination = tmp_path / "OllamaSetup.exe"
    result = stage_ollama_installer(destination, consent=True, client=Client())

    assert result == destination
    assert destination.stat().st_size == 1000


def test_installer_download_still_caps_an_unexpectedly_huge_response(tmp_path: Path, monkeypatch):
    """The cap must still trigger for something clearly wrong (e.g. a
    misbehaving/redirected response streaming forever) -- raising the
    limit for a realistic installer shouldn't make it unbounded. Patches
    the limit down so this doesn't need to actually write gigabytes."""
    import hanarr.ollama_setup as ollama_setup_mod

    monkeypatch.setattr(ollama_setup_mod, "OLLAMA_INSTALLER_MAX_BYTES", 1024)

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def raise_for_status(self):
            return None

        def iter_bytes(self, chunk_size):
            chunk = b"x" * chunk_size
            while True:
                yield chunk

    class Client:
        def stream(self, *args, **kwargs):
            return Response()

    destination = tmp_path / "OllamaSetup.exe"
    with pytest.raises(SetupError, match="byte safety limit"):
        stage_ollama_installer(destination, consent=True, client=Client())

    assert not destination.exists()


def test_model_pull_requires_consent_without_contacting_service():
    with pytest.raises(SetupError, match="consent"):
        pull_model("qwen2.5:7b", "http://localhost:11434", consent=False)
