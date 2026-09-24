"""Small, explicit routing boundary for specialized local-first agents."""
from __future__ import annotations

from dataclasses import dataclass

from .config import AgentName, AgentRoute, LLMConfig, Settings
from .llm import build_llm_client
from .llm.base import LLMClient


@dataclass(frozen=True)
class ResolvedAgent:
    name: str
    task: str | None
    config: LLMConfig


def _merge(base: LLMConfig, *routes: AgentRoute) -> LLMConfig:
    values = base.model_dump()
    for route in routes:
        for key, value in route.model_dump().items():
            if value is not None:
                values[key] = value
    return LLMConfig(**values)


class AgentOrchestrator:
    """Resolve a role/task to one provider without changing service contracts.

    This foundation deliberately sequences no work and grants no tools. It only
    makes provider/model selection explicit and keeps ``none`` deterministic.
    """

    def __init__(self, settings: Settings, client_builder=build_llm_client):
        self.settings = settings
        self._client_builder = client_builder

    def resolve(self, agent: AgentName, task: str | None = None) -> ResolvedAgent:
        if not hasattr(self.settings.agents, agent):
            raise ValueError(f"Unknown specialized agent: {agent!r}")
        role_route = getattr(self.settings.agents, agent)
        task_value = self.settings.agents.tasks.get(task) if task else None
        task_route = AgentRoute.model_validate(task_value) if task_value is not None else None
        config = _merge(self.settings.llm, self.settings.agents.default, role_route, task_route or AgentRoute())
        return ResolvedAgent(name=agent, task=task, config=config)

    def client_for(self, agent: AgentName, task: str | None = None) -> LLMClient:
        resolved = self.resolve(agent, task)
        return self._client_builder(resolved.config)
