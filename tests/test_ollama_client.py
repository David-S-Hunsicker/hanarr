import httpx

from hanarr.config import LLMConfig
from hanarr.llm.factory import build_llm_client
from hanarr.llm.ollama_client import OllamaClient


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_complete_json_sends_num_ctx_so_ollama_does_not_silently_truncate_long_prompts(monkeypatch):
    """Regression test: without an explicit num_ctx, Ollama caps the actual
    context window at its own default (observed: 4096 tokens) regardless of
    what the model natively supports, and silently truncates anything
    longer instead of erroring -- which can drop the system prompt or the
    input itself and produce a degenerate response. This was the real
    cause behind repeated "LLM returned no rationale alongside a score of
    0.0" failures on long resumes/postings, previously assumed to be pure
    model unreliability."""
    captured = {}

    def fake_post(url, json, timeout):
        captured["json"] = json
        return _FakeResponse({"message": {"content": "{}"}})

    monkeypatch.setattr(httpx, "post", fake_post)

    client = OllamaClient(model="qwen2.5:14b", num_ctx=8192)
    client.complete_json("system prompt", "user prompt")

    assert captured["json"]["options"] == {"num_ctx": 8192}


def test_complete_json_uses_a_custom_num_ctx_when_given(monkeypatch):
    captured = {}

    def fake_post(url, json, timeout):
        captured["json"] = json
        return _FakeResponse({"message": {"content": "{}"}})

    monkeypatch.setattr(httpx, "post", fake_post)

    client = OllamaClient(model="qwen2.5:14b", num_ctx=16384)
    client.complete_json("system prompt", "user prompt")

    assert captured["json"]["options"] == {"num_ctx": 16384}


def test_build_llm_client_passes_num_ctx_from_config_through_to_ollama_client():
    cfg = LLMConfig(provider="ollama", model="qwen2.5:14b", num_ctx=12345)

    client = build_llm_client(cfg)

    assert isinstance(client, OllamaClient)
    assert client.num_ctx == 12345


def test_llm_config_defaults_num_ctx_well_above_ollamas_own_default():
    """Ollama's own default (observed: 4096) is too small for a realistic
    fit-scoring prompt (a full resume plus a job posting, each capped at
    8000 characters, plus the system prompt) -- confirmed by measuring a
    synthetic prompt of that shape at roughly 3300-3700 tokens, leaving
    little headroom before truncation. The default here must stay safely
    above that Ollama default, not just "some positive number"."""
    assert LLMConfig().num_ctx >= 8192
