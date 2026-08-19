from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .config import Settings
from .connectivity import assert_loopback_http_url


class LlamaCppClient:
    """Client for Kendra's local llama.cpp OpenAI-compatible server.

    The URL is expected to be loopback-only. Kendra never enables llama.cpp's
    server-side filesystem/shell tools. Agent tools are implemented and
    validated by Kendra code instead.
    """

    def __init__(self, settings: Settings):
        self.base_url = str(settings.require("llm.base_url")).rstrip("/")
        assert_loopback_http_url(self.base_url)
        self.model = str(settings.require("llm.model"))
        self.timeout = float(settings.get("llm.timeout_seconds", 120))
        self.temperature = float(settings.get("llm.temperature", 0.35))
        self.max_tokens = int(settings.get("llm.max_tokens", 700))
        self.repeat_penalty = float(settings.get("llm.repeat_penalty", 1.15))
        self.presence_penalty = float(settings.get("llm.presence_penalty", 0.6))
        self.frequency_penalty = float(settings.get("llm.frequency_penalty", 0.4))
        self.top_p = float(settings.get("llm.top_p", 0.8))
        self.top_k = int(settings.get("llm.top_k", 20))
        self.min_p = float(settings.get("llm.min_p", 0.0))
        # Wide penalty window so tokens from Kendra's own recent replies in
        # the history note are penalized during generation; the default 64
        # only covers freshly generated text, which is why she could quote
        # her previous answer verbatim.
        self.repeat_last_n = int(settings.get("llm.repeat_last_n", 512))

    def _offline_error(self, exc: Exception) -> RuntimeError:
        """Turn a bare connection failure into something a person can act on.

        ``ConnectError: All connection attempts failed`` told the desktop user
        nothing. The only cause is that Kendra's local llama.cpp server is not
        listening, so say that and say how to fix it.
        """
        return RuntimeError(
            f"Kendra's local text brain is not running at {self.base_url}. "
            "Start it with scripts/start_llm_intel_macos.sh (macOS) or "
            "`systemctl start kendra-llm` (Raspberry Pi). "
            f"No response was sent anywhere else. [{type(exc).__name__}]"
        )

    async def slot_states(self) -> list[dict[str, Any]]:
        root = self.base_url.removesuffix("/v1")
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{root}/slots")
            response.raise_for_status()
            return list(response.json())

    async def slot_action(self, slot_id: int, action: str, filename: str) -> bool:
        """Libra-style KV persistence (SenSys'26): saving/restoring a prompt
        prefix's KV cache to disk turns an 18s re-prefill into a 0.03s load.
        The automatic --cache-idle-slots flag is pathological on this build
        (16s/token save churn), so Kendra orchestrates saves explicitly."""
        root = self.base_url.removesuffix("/v1")
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{root}/slots/{slot_id}?action={action}", json={"filename": filename}
            )
            return response.status_code == 200

    async def health(self) -> bool:
        root = self.base_url.removesuffix("/v1")
        async with httpx.AsyncClient(timeout=5) as client:
            try:
                response = await client.get(f"{root}/health")
                return 200 <= response.status_code < 300
            except httpx.HTTPError:
                return False

    def _payload(
        self,
        messages: list[dict[str, Any]],
        *,
        stream: bool,
        response_schema: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        thinking: bool = False,
        id_slot: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
            "stream": stream,
            # A 0.6B model with a long system prompt collapses onto one canned
            # sentence without these. Penalties discourage reciting the same
            # self-introduction every turn; top_p keeps it from going loose.
            "repeat_penalty": self.repeat_penalty,
            "presence_penalty": self.presence_penalty,
            "frequency_penalty": self.frequency_penalty,
            "top_p": self.top_p,
            "top_k": self.top_k,
            "min_p": self.min_p,
            "repeat_last_n": self.repeat_last_n,
            # Qwen3's hybrid thinking: off for ordinary turns (latency), on
            # per request for hard questions. The server's --reasoning-budget
            # caps runaway chains either way.
            "chat_template_kwargs": {"enable_thinking": bool(thinking)},
        }
        if thinking:
            # Official thinking-mode sampling differs from non-thinking.
            payload["temperature"] = 0.6 if temperature is None else temperature
            payload["top_p"] = 0.95
        if response_schema is not None:
            payload["response_format"] = {"type": "json_object", "schema": response_schema}
        if id_slot is not None:
            # Deterministic slot ownership (Libra by construction): restored
            # KV is invisible to the prefix router, so requests must pin
            # their slot. Conversation owns slot 0; planner/consolidation
            # share slot 1 — chat's warm prefix is never evicted by tools.
            payload["id_slot"] = int(id_slot)
        return payload

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        response_schema: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        thinking: bool = False,
        id_slot: int | None = None,
    ) -> str:
        payload = self._payload(
            messages,
            stream=False,
            response_schema=response_schema,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking=thinking,
            id_slot=id_slot,
        )
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(f"{self.base_url}/chat/completions", json=payload)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                raise self._offline_error(exc) from exc
            response.raise_for_status()
            data = response.json()
        return str(data["choices"][0]["message"]["content"])

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        thinking: bool = False,
        id_slot: int | None = None,
    ) -> AsyncIterator[str]:
        """Yield final-answer text deltas from the local llama.cpp SSE stream.

        With the default --reasoning-format, thinking tokens arrive in
        delta.reasoning_content, so yielded content deltas stay clean.
        """

        payload = self._payload(
            messages,
            stream=True,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking=thinking,
            id_slot=id_slot,
        )
        timeout = httpx.Timeout(self.timeout, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                async with client.stream("POST", f"{self.base_url}/chat/completions", json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line or line.startswith(":"):
                            continue
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            event = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        choices = event.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        text = delta.get("content")
                        if isinstance(text, str) and text:
                            yield text
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                raise self._offline_error(exc) from exc
