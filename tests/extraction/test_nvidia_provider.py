"""
NvidiaProvider model chain, served through httpx.MockTransport (no network).

The 200 answer is the real BSF answer recorded in tests/fixtures/llm, wrapped in the
OpenAI-compatible chat.completion envelope the provider parses. Status codes are the ones
integrate.api.nvidia.com returned: nvidia/nemotron-3-super-120b-a12b answers 410 Gone since
03-10-2026, and the free tier rate-limits with 429. No response body text is invented: error
responses carry no body.
"""
import json
from pathlib import Path

import httpx
import pytest

from govcareeros.backend.config.settings import Settings, settings
from govcareeros.backend.extraction.llm_extractor import SCHEMA, SYSTEM
from govcareeros.backend.llm import provider as provider_module
from govcareeros.backend.llm.provider import LLMError, NvidiaProvider, get_provider

LLM = Path(__file__).parents[1] / "fixtures" / "llm"
RECORDING = json.loads((LLM / "BSF_e0ee6e833f.nemotron-3-super-120b-a12b.llm.json").read_text(encoding="utf-8"))
ANSWER = RECORDING["chunks"][0]["raw"]

RETIRED = "nvidia/nemotron-3-super-120b-a12b"
ULTRA = "nvidia/nemotron-3-ultra-550b-a55b"
LIGHTNING = "nvidia/nemotron-3.5-lightning-30b-a3b"

def _completion(model):
    return httpx.Response(200, json={"object": "chat.completion", "model": model, "choices": [
        {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(ANSWER)}}]})

class Served:
    """Answers per model from a queue of status codes (200 = the recorded answer); records every request."""

    def __init__(self, table):
        self.queues = {m: list(v) for m, v in table.items()}
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        status = self.queues[body["model"]].pop(0)
        if isinstance(status, httpx.Response):
            return status
        return _completion(body["model"]) if status == 200 else httpx.Response(status)

    @property
    def models(self):
        return [b["model"] for b in self.requests]

@pytest.fixture
def sleeps(monkeypatch):
    waited = []

    async def fake_sleep(seconds):
        waited.append(seconds)

    monkeypatch.setattr(provider_module.asyncio, "sleep", fake_sleep)
    return waited

def _provider(served, models=(ULTRA, LIGHTNING), rpm=0):
    return NvidiaProvider(list(models), "test-key", settings.NVIDIA_BASE_URL, 30, requests_per_minute=rpm,
                          transport=httpx.MockTransport(served))

@pytest.mark.asyncio
async def test_retired_model_falls_back_and_is_remembered_for_the_run(sleeps):
    served = Served({RETIRED: [410], ULTRA: [200, 200]})
    p = _provider(served, models=(RETIRED, ULTRA, LIGHTNING))
    resp = await p.complete_json(SYSTEM, "<document></document>", SCHEMA)
    assert resp.data == ANSWER and resp.model == ULTRA and p.model == ULTRA
    assert p.unavailable == {RETIRED: "HTTP 410"}
    # The next call does not ask the retired model again
    await p.complete_json(SYSTEM, "<document></document>", SCHEMA)
    assert served.models == [RETIRED, ULTRA, ULTRA]
    assert sleeps == []
    # Hosted API shape: json_schema response_format (nvext.guided_json is refused with 400), thinking off
    body = served.requests[-1]
    assert body["response_format"]["type"] == "json_schema" and body["response_format"]["json_schema"]["schema"] == SCHEMA
    assert body["chat_template_kwargs"] == {"enable_thinking": False} and "nvext" not in body

@pytest.mark.asyncio
async def test_not_found_moves_to_next_model(sleeps):
    served = Served({ULTRA: [404], LIGHTNING: [200]})
    p = _provider(served)
    resp = await p.complete_json(SYSTEM, "<document></document>", SCHEMA)
    assert resp.model == LIGHTNING and p.model == LIGHTNING and served.models == [ULTRA, LIGHTNING]

@pytest.mark.asyncio
async def test_rate_limit_backs_off_on_the_same_model(sleeps):
    served = Served({ULTRA: [429, httpx.Response(429, headers={"Retry-After": "20"}), 200]})
    p = _provider(served)
    resp = await p.complete_json(SYSTEM, "<document></document>", SCHEMA)
    # 429 is not a reason to switch model: same model, exponential backoff, Retry-After honoured
    assert resp.model == ULTRA and served.models == [ULTRA, ULTRA, ULTRA] and p.unavailable == {}
    assert sleeps == [15, 20.0]

@pytest.mark.asyncio
async def test_rate_limit_that_does_not_clear_fails_without_switching(sleeps):
    served = Served({ULTRA: [429, 429, 429], LIGHTNING: []})
    with pytest.raises(LLMError, match=r"failed after 3 attempts \(nvidia/nemotron-3-ultra-550b-a55b\): HTTP 429"):
        await _provider(served).complete_json(SYSTEM, "<document></document>", SCHEMA)
    assert served.models == [ULTRA] * 3 and sleeps == [15, 30, 60]

@pytest.mark.asyncio
async def test_requests_are_spaced_to_the_per_minute_limit(sleeps):
    served = Served({ULTRA: [200, 200]})
    p = _provider(served, rpm=40)
    await p.complete_json(SYSTEM, "<document></document>", SCHEMA)
    assert sleeps == []
    await p.complete_json(SYSTEM, "<document></document>", SCHEMA)
    assert len(sleeps) == 1 and 1.0 < sleeps[0] <= 1.5  # 60 s / 40 requests

@pytest.mark.asyncio
async def test_every_model_gone_is_an_error_naming_them(sleeps):
    served = Served({ULTRA: [410], LIGHTNING: [404]})
    p = _provider(served)
    with pytest.raises(LLMError, match=r"no NVIDIA model available: .*ultra.*\(HTTP 410\); .*lightning.*\(HTTP 404\)"):
        await p.complete_json(SYSTEM, "<document></document>", SCHEMA)
    with pytest.raises(LLMError, match="no NVIDIA model available"):
        await p.complete_json(SYSTEM, "<document></document>", SCHEMA)
    assert served.models == [ULTRA, LIGHTNING]

@pytest.mark.asyncio
async def test_bad_request_is_not_treated_as_a_missing_model(sleeps):
    served = Served({ULTRA: [400], LIGHTNING: [200]})
    p = _provider(served)
    with pytest.raises(LLMError, match=r"NVIDIA API 400 \(nvidia/nemotron-3-ultra-550b-a55b\)"):
        await p.complete_json(SYSTEM, "<document></document>", SCHEMA)
    assert served.models == [ULTRA] and p.unavailable == {}

def test_configured_chain_and_override(monkeypatch):
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "test-key")
    assert Settings.model_fields["NVIDIA_MODELS"].default == f"{ULTRA},{LIGHTNING}"
    monkeypatch.setattr(settings, "NVIDIA_MODELS", Settings.model_fields["NVIDIA_MODELS"].default)
    monkeypatch.setattr(settings, "LLM_MODEL", RETIRED)  # LLM_MODEL is the Ollama model; NVIDIA ignores it
    p = get_provider("nvidia")
    assert p.models == [ULTRA, LIGHTNING] and p.model == ULTRA
    p = get_provider("nvidia", f" {LIGHTNING}, {ULTRA},{LIGHTNING}")
    assert p.models == [LIGHTNING, ULTRA]
    monkeypatch.setattr(settings, "NVIDIA_MODELS", " , ")
    with pytest.raises(LLMError, match="NVIDIA_MODELS is empty"):
        get_provider("nvidia")
