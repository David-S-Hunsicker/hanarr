from __future__ import annotations

import httpx

from .base import LLMClient


class OllamaClient(LLMClient):
    """Talks to a local Ollama server (https://ollama.com). Install it,
    `ollama pull <model>`, and it's ready — no API key, no network calls
    beyond localhost."""

    def __init__(self, model: str, base_url: str = "http://localhost:11434", timeout: float = 120.0):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def complete_json(self, system: str, user: str) -> str:
        resp = httpx.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "format": "json",
                "stream": False,
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        try:
            return data["message"]["content"]
        except (KeyError, TypeError) as e:
            raise RuntimeError(f"Unexpected Ollama response shape: {data}") from e
