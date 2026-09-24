from pathlib import Path

import httpx
import pytest

from hanarr.ollama_setup import (
    HardwareInfo,
    SetupCancelled,
    SetupError,
    detect_ollama,
    pull_model,
    recommend_model,
    stage_ollama_installer,
)


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


def test_detect_ollama_is_usable_when_service_is_unavailable(monkeypatch, tmp_path: Path):
    def fail(*args, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr("hanarr.ollama_setup.shutil.which", lambda _: None)
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


def test_model_pull_requires_consent_without_contacting_service():
    with pytest.raises(SetupError, match="consent"):
        pull_model("qwen2.5:7b", "http://localhost:11434", consent=False)
