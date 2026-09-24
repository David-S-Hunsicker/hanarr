from pathlib import Path

import httpx

from jobcopilot.ollama_setup import HardwareInfo, detect_ollama, recommend_model


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

    monkeypatch.setattr("jobcopilot.ollama_setup.shutil.which", lambda _: r"C:\Ollama\ollama.exe")
    monkeypatch.setattr("jobcopilot.ollama_setup._executable_details", lambda _: "ollama version 0.1")
    monkeypatch.setattr("jobcopilot.ollama_setup.httpx.get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(
        "jobcopilot.ollama_setup.detect_hardware",
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

    monkeypatch.setattr("jobcopilot.ollama_setup.shutil.which", lambda _: None)
    monkeypatch.setattr("jobcopilot.ollama_setup.httpx.get", fail)
    monkeypatch.setattr(
        "jobcopilot.ollama_setup.detect_hardware",
        lambda _: HardwareInfo(None, None, "unknown"),
    )

    diagnostics = detect_ollama("qwen2.5:7b", data_path=tmp_path)

    assert diagnostics.executable_path is None
    assert diagnostics.service_reachable is False
    assert diagnostics.service_error == "offline"
    assert diagnostics.configured_model_available is False
