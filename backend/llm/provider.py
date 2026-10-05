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
from typing import Any, Dict, List, Optional, Sequence, Union

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

def model_list(value: str) -> List[str]:
    """'a, b,a' -> ['a', 'b']: a comma-separated model chain, order kept, duplicates dropped."""
    return list(dict.fromkeys(m.strip() for m in value.split(",") if m.strip()))

class NvidiaProvider:
    """
    build.nvidia.com hosted models via the OpenAI-compatible API, with strict JSON-schema output.

    `models` is tried in order. A model the API reports as not found (404) or retired (410) is
    dropped for the rest of this provider's life (one script run) and the next one is used;
    `self.model` always names the model that answers now, so drafts record what produced them.
    429 (free tier: ~40 requests/minute) and 5xx are retried on the same model with backoff,
    honouring Retry-After. Thinking is switched off: the answer budget goes to the JSON.
    """

    name = "nvidia"
    chunk_chars = 120_000  # long-context models: send the whole notice in one call

    def __init__(self, models: Union[str, Sequence[str]], api_key: str, base_url: str, timeout: float, retries: int = 3,
                 requests_per_minute: float = 40, transport: Optional[httpx.AsyncBaseTransport] = None):
        if not api_key:
            raise LLMError("NVIDIA_API_KEY is not set in .env")
        self.models = model_list(models) if isinstance(models, str) else list(dict.fromkeys(models))
        if not self.models:
            raise LLMError("no NVIDIA model configured (NVIDIA_MODELS is empty)")
        self.unavailable: Dict[str, str] = {}  # model -> why it was dropped, for this run
        self.base_url, self.timeout, self.retries = base_url.rstrip("/"), timeout, retries
        self.min_interval = 60.0 / requests_per_minute if requests_per_minute > 0 else 0.0
        self._transport = transport
        self._last_request: Optional[float] = None
        self._headers = {"Authorization": f"Bearer {api_key}"}

    @property
    def model(self) -> str:
        """The first model of the chain not yet found unavailable (the last one once all are gone)."""
        return next((m for m in self.models if m not in self.unavailable), self.models[-1])

    async def _pace(self) -> None:
        """Space request starts so a run stays under the per-minute limit instead of collecting 429s."""
        if self._last_request is not None:
            wait = self._last_request + self.min_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
        self._last_request = time.monotonic()

    @staticmethod
    def _retry_after(r: httpx.Response, fallback: float) -> float:
        try:
            return max(float(r.headers.get("retry-after", "")), 1.0)
        except ValueError:
            return fallback

    async def complete_json(self, system: str, user: str, schema: Dict[str, Any]) -> LLMResponse:
        body = {
            "temperature": 0, "max_tokens": 16384,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "extraction", "schema": schema, "strict": True}},
            "chat_template_kwargs": {"enable_thinking": False},
        }
        started = time.monotonic()
        async with httpx.AsyncClient(timeout=self.timeout, transport=self._transport) as client:
            for model in [m for m in self.models if m not in self.unavailable]:
                payload = await self._post(client, {**body, "model": model}, model)
                if payload is None:  # 404/410: next model in the chain
                    continue
                content = (payload["choices"][0]["message"].get("content") or "").strip()
                if not content:
                    raise LLMError("model returned no content (output budget likely spent on reasoning)")
                try:
                    data = json.loads(content)
                except json.JSONDecodeError as e:
                    raise LLMError(f"model did not return valid JSON: {content[:300]}") from e
                usage = payload.get("usage") or {}
                return LLMResponse(data, model, round(time.monotonic() - started, 1),
                                   usage.get("prompt_tokens"), usage.get("completion_tokens"))
        raise LLMError("no NVIDIA model available: " + "; ".join(f"{m} ({why})" for m, why in self.unavailable.items()))

    async def _post(self, client: httpx.AsyncClient, body: Dict[str, Any], model: str) -> Optional[Dict[str, Any]]:
        """The response payload, or None after marking `model` unavailable (404/410)."""
        last = ""
        for attempt in range(self.retries):
            await self._pace()
            try:
                r = await client.post(f"{self.base_url}/chat/completions", headers=self._headers, json=body)
            except httpx.HTTPError as e:
                last = f"{type(e).__name__}: {e}"
                await asyncio.sleep(5 * (attempt + 1))
                continue
            if r.status_code in (404, 410):
                self.unavailable[model] = f"HTTP {r.status_code}"
                return None
            if r.status_code == 429:
                last = f"HTTP 429: {r.text[:200]}"
                await asyncio.sleep(self._retry_after(r, 15 * 2 ** attempt))
                continue
            if r.status_code in (500, 502, 503, 504):
                last = f"HTTP {r.status_code}: {r.text[:200]}"
                await asyncio.sleep(10 * (attempt + 1))
                continue
            if r.status_code != 200:
                raise LLMError(f"NVIDIA API {r.status_code} ({model}): {r.text[:300]}")
            return r.json()
        raise LLMError(f"NVIDIA API failed after {self.retries} attempts ({model}): {last}")

def get_provider(provider: Optional[str] = None, model: Optional[str] = None):
    """
    `model` overrides the configured model. For NVIDIA it may be a comma-separated chain and
    replaces NVIDIA_MODELS; LLM_MODEL is the Ollama model and is not used for NVIDIA.
    """
    name = (provider or settings.LLM_PROVIDER).lower()
    if name == "ollama":
        return OllamaProvider(model or settings.LLM_MODEL, settings.OLLAMA_URL, settings.LLM_TIMEOUT_SECONDS, settings.LLM_NUM_CTX)
    if name == "nvidia":
        return NvidiaProvider(model or settings.NVIDIA_MODELS, settings.NVIDIA_API_KEY.strip(), settings.NVIDIA_BASE_URL,
                              settings.LLM_TIMEOUT_SECONDS, requests_per_minute=settings.NVIDIA_REQUESTS_PER_MINUTE)
    raise LLMError(f"LLM_PROVIDER={name!r} is not implemented; supported: 'ollama', 'nvidia'")
