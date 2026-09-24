from __future__ import annotations

import httpx

from .base import LLMClient

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"


class AnthropicClient(LLMClient):
    """Optional: use a hosted Claude model instead of a local one. Needs
    ANTHROPIC_API_KEY (set in .env) and will incur API usage costs — most
    users of this project are expected to use the Ollama default instead."""

    def __init__(self, model: str, api_key: str, timeout: float = 60.0):
        if not api_key:
            raise ValueError(
                "llm.provider is 'anthropic' but no API key was found. Set "
                "ANTHROPIC_API_KEY in your .env file."
            )
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def complete_json(self, system: str, user: str) -> str:
        resp = httpx.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": 1024,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        try:
            return data["content"][0]["text"]
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"Unexpected Anthropic response shape: {data}") from e
