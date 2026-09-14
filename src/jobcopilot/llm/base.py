"""LLM client interface. Every provider (Ollama, Anthropic, ...) implements
this so the rest of the app never cares which one is configured."""
from __future__ import annotations

from abc import ABC, abstractmethod


class LLMClient(ABC):
    @abstractmethod
    def complete_json(self, system: str, user: str) -> str:
        """Return a JSON string produced by the model given a system and
        user prompt. Callers are responsible for parsing/validating it."""
        raise NotImplementedError


class NullLLMClient(LLMClient):
    """Used when llm.provider = 'none'. Fit-scoring falls back to a plain
    keyword-overlap heuristic instead of calling any model — see
    matching.py's rule_based_score()."""

    def complete_json(self, system: str, user: str) -> str:
        raise RuntimeError(
            "No LLM configured (llm.provider = 'none'). This is expected — "
            "callers should use the rule-based fallback instead of calling "
            "complete_json()."
        )
