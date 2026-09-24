from pathlib import Path

import pytest

from jobcopilot.agent_orchestration import AgentOrchestrator
from jobcopilot.config import Settings, load_settings, save_settings_to_yaml
from jobcopilot.llm.base import NullLLMClient


def test_routes_role_and_task_overrides_over_local_default():
    settings = Settings()
    settings.llm.model = "global"
    settings.agents.default.model = "default"
    settings.agents.evaluator.provider = "anthropic"
    settings.agents.evaluator.model = "evaluator"
    settings.agents.tasks["project_review"] = {"provider": "ollama", "model": "task"}
    seen = []

    orchestrator = AgentOrchestrator(settings, client_builder=lambda config: seen.append(config) or NullLLMClient())
    resolved = orchestrator.resolve("evaluator", "project_review")
    orchestrator.client_for("evaluator", "project_review")

    assert resolved.config.provider == "ollama"
    assert resolved.config.model == "task"
    assert seen[-1].provider == "ollama"


def test_unconfigured_routes_inherit_ollama_and_none_is_deterministic():
    settings = Settings()
    settings.llm.provider = "none"
    orchestrator = AgentOrchestrator(settings)

    resolved = orchestrator.resolve("profiler")

    assert resolved.config.provider == "none"
    assert isinstance(orchestrator.client_for("profiler"), NullLLMClient)


def test_unknown_agent_is_an_explicit_error():
    with pytest.raises(ValueError, match="Unknown specialized agent"):
        AgentOrchestrator(Settings()).resolve("not_an_agent")


def test_route_api_keys_are_not_written_to_yaml(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    settings = Settings()
    settings.agents.evaluator.provider = "anthropic"
    settings.agents.evaluator.api_key = "secret"
    save_settings_to_yaml(settings, config_path)

    assert "secret" not in config_path.read_text()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    loaded = load_settings(config_path)
    assert loaded.agents.evaluator.api_key == "from-env"
