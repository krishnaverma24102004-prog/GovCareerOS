"""
LLM provider abstraction. Only providers that are configured and tested are implemented;
asking for another one fails loudly instead of silently doing something else.

The LLM is used only to *locate* information in official documents (value + verbatim quote
+ page). Everything stored is re-verified against the document text; see backend/extraction.
Only public notice text is ever sent to a provider, never user profile data.
"""
import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

from govcareeros.backend.config.settings import settings

@dataclass
class LLMResponse:
    data: Dict[str, Any]
    model: str
    seconds: float
    prompt_tokens: Optional[int] = None
    output_tokens: Optional[int] = None

class LLMError(RuntimeError):
    pass

class OllamaProvider:
    """Local Ollama server. No data leaves the machine unless the model is an Ollama *cloud* model."""

    name = "ollama"
    chunk_chars = 5000  # small local context window

    def __init__(self, model: str, base_url: str, timeout: float, num_ctx: int):
        self.model, self.base_url, self.timeout, self.num_ctx = model, base_url.rstrip("/"), timeout, num_ctx

    async def complete_json(self, system: str, user: str, schema: Dict[str, Any]) -> LLMResponse:
        started = time.monotonic()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                r = await client.post(f"{self.base_url}/api/chat", json={
                    "model": self.model, "stream": False, "think": False, "format": schema,
                    "options": {"temperature": 0, "num_ctx": self.num_ctx},
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                })
            except httpx.HTTPError as e:
                raise LLMError(f"Ollama not reachable at {self.base_url}: {type(e).__name__}: {e}") from e
        if r.status_code != 200:
            raise LLMError(f"Ollama returned {r.status_code}: {r.text[:300]}")
        body = r.json()
        try:
            data = json.loads(body["message"]["content"])
        except (KeyError, json.JSONDecodeError) as e:
            raise LLMError(f"model did not return valid JSON: {body.get('message', {}).get('content', '')[:300]}") from e
        return LLMResponse(data, self.model, round(time.monotonic() - started, 1),
                           body.get("prompt_eval_count"), body.get("eval_count"))

class NvidiaProvider:
    """build.nvidia.com hosted models via the OpenAI-compatible API, with strict JSON-schema output."""

    name = "nvidia"
    chunk_chars = 120_000  # long-context models: send the whole notice in one call

    def __init__(self, model: str, api_key: str, base_url: str, timeout: float, retries: int = 3):
        if not api_key:
            raise LLMError("NVIDIA_API_KEY is not set in .env")
        self.model, self.base_url, self.timeout, self.retries = model, base_url.rstrip("/"), timeout, retries
        self._headers = {"Authorization": f"Bearer {api_key}"}

    async def complete_json(self, system: str, user: str, schema: Dict[str, Any]) -> LLMResponse:
        body = {
            "model": self.model, "temperature": 0, "max_tokens": 16384,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "extraction", "schema": schema, "strict": True}},
        }
        started = time.monotonic()
        last = ""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(self.retries):
                try:
                    r = await client.post(f"{self.base_url}/chat/completions", headers=self._headers, json=body)
                except httpx.HTTPError as e:
                    last = f"{type(e).__name__}: {e}"
                    await asyncio.sleep(5 * (attempt + 1))
                    continue
                if r.status_code in (429, 500, 502, 503, 504):
                    last = f"HTTP {r.status_code}: {r.text[:200]}"
                    await asyncio.sleep(10 * (attempt + 1))
                    continue
                if r.status_code != 200:
                    raise LLMError(f"NVIDIA API {r.status_code}: {r.text[:300]}")
                payload = r.json()
                content = (payload["choices"][0]["message"].get("content") or "").strip()
                if not content:
                    raise LLMError("model returned no content (output budget likely spent on reasoning)")
                try:
                    data = json.loads(content)
                except json.JSONDecodeError as e:
                    raise LLMError(f"model did not return valid JSON: {content[:300]}") from e
                usage = payload.get("usage") or {}
                return LLMResponse(data, self.model, round(time.monotonic() - started, 1),
                                   usage.get("prompt_tokens"), usage.get("completion_tokens"))
        raise LLMError(f"NVIDIA API failed after {self.retries} attempts: {last}")

def get_provider(provider: Optional[str] = None, model: Optional[str] = None):
    name = (provider or settings.LLM_PROVIDER).lower()
    model = model or settings.LLM_MODEL
    if name == "ollama":
        return OllamaProvider(model, settings.OLLAMA_URL, settings.LLM_TIMEOUT_SECONDS, settings.LLM_NUM_CTX)
    if name == "nvidia":
        return NvidiaProvider(model, settings.NVIDIA_API_KEY.strip(), settings.NVIDIA_BASE_URL, settings.LLM_TIMEOUT_SECONDS)
    raise LLMError(f"LLM_PROVIDER={name!r} is not implemented; supported: 'ollama', 'nvidia'")
