from __future__ import annotations

from ..config import LLMConfig
from .base import LLMClient, NullLLMClient


def build_llm_client(cfg: LLMConfig) -> LLMClient:
    if cfg.provider == "ollama":
        from .ollama_client import OllamaClient

        return OllamaClient(model=cfg.model, base_url=cfg.base_url)
    if cfg.provider == "anthropic":
        from .anthropic_client import AnthropicClient

        return AnthropicClient(model=cfg.model, api_key=cfg.api_key or "")
    if cfg.provider == "none":
        return NullLLMClient()
    raise ValueError(f"Unknown llm.provider: {cfg.provider!r}")
