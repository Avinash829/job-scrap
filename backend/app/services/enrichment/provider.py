"""LLM provider abstraction + Gemini implementation.

Calls the REST endpoint with httpx directly rather than the google-genai SDK:
one less dependency, and it keeps the key-pool rotation explicit instead of
hidden inside a client object.
"""
from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx

from app.core.config import get_settings
from app.services.enrichment.key_pool import AllKeysExhausted, KeyPool

log = logging.getLogger(__name__)

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"


class LLMUnavailable(RuntimeError):
    """No usable key/model. Caller degrades to rules-only."""


class LLMProvider(ABC):
    @abstractmethod
    async def generate_json(self, prompt: str, schema: dict) -> Any:
        """Return parsed JSON conforming to `schema`."""

    @abstractmethod
    async def list_models(self) -> list[str]: ...


class GeminiProvider(LLMProvider):
    """Structured-output Gemini client with key and model fallback.

    Model fallback matters because free-tier model availability shifts: a name
    that worked last month can 404. On a 404 we demote to the next candidate
    and remember the choice for the rest of the process.
    """

    def __init__(
        self,
        keys: list[str] | None = None,
        model: str | None = None,
        fallback_models: list[str] | None = None,
        timeout: float | None = None,
    ) -> None:
        s = get_settings()
        self.pool = KeyPool(keys if keys is not None else s.gemini_api_keys)
        self._candidates = [model or s.gemini_model, *(fallback_models or s.gemini_fallback_models)]
        self._candidates = list(dict.fromkeys(self._candidates))
        self._active_model: str | None = None
        self.timeout = timeout or s.http_timeout
        self._calls = 0

    @property
    def enabled(self) -> bool:
        return self.pool.has_keys

    @property
    def active_model(self) -> str | None:
        return self._active_model

    @property
    def call_count(self) -> int:
        return self._calls

    # ------------------------------------------------------------------ models

    async def list_models(self) -> list[str]:
        state = self.pool.acquire()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.get(f"{GEMINI_BASE}/models", params={"key": state.key})
            r.raise_for_status()
        names = []
        for m in r.json().get("models", []):
            if "generateContent" in (m.get("supportedGenerationMethods") or []):
                names.append(str(m.get("name", "")).removeprefix("models/"))
        return sorted(names)

    async def resolve_model(self) -> str:
        """Pick the first candidate the key can actually use."""
        if self._active_model:
            return self._active_model
        try:
            available = set(await self.list_models())
        except Exception as exc:  # noqa: BLE001 - fall back to blind attempts
            log.warning("could not list models (%s); trying candidates blindly", exc)
            self._active_model = self._candidates[0]
            return self._active_model

        for name in self._candidates:
            if name in available:
                self._active_model = name
                log.info("gemini model resolved: %s", name)
                return name

        # nothing matched - prefer a flash-family model over a pro one, since
        # pro burns free quota far faster
        flash = sorted(n for n in available if "flash" in n and "thinking" not in n)
        if flash:
            self._active_model = flash[0]
            log.warning("configured models unavailable; using %s", flash[0])
            return flash[0]
        raise LLMUnavailable(f"no usable model; key offers: {sorted(available)[:10]}")

    # ---------------------------------------------------------------- generate

    async def generate_json(self, prompt: str, schema: dict) -> Any:
        if not self.enabled:
            raise LLMUnavailable("no Gemini API keys configured")

        model = await self.resolve_model()
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.0,          # extraction, not creativity
                "responseMimeType": "application/json",
                "responseSchema": schema,
                "maxOutputTokens": 8192,
            },
        }

        last_error: Exception | None = None
        for _ in range(len(self.pool) * 2 or 1):
            try:
                state = self.pool.acquire()
            except AllKeysExhausted as exc:
                raise LLMUnavailable(str(exc)) from exc

            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    r = await client.post(
                        f"{GEMINI_BASE}/models/{model}:generateContent",
                        params={"key": state.key},
                        json=body,
                    )

                if r.status_code in (429, 403):
                    self.pool.report_failure(state, rate_limited=True)
                    last_error = RuntimeError(f"HTTP {r.status_code}")
                    continue
                if r.status_code == 404:
                    # this model name is gone - demote and retry once
                    self._candidates = [c for c in self._candidates if c != model]
                    self._active_model = None
                    if not self._candidates:
                        raise LLMUnavailable(f"model {model} not found and no fallbacks left")
                    model = await self.resolve_model()
                    continue
                if r.status_code >= 500:
                    self.pool.report_failure(state, rate_limited=False)
                    last_error = RuntimeError(f"HTTP {r.status_code}")
                    await asyncio.sleep(2)
                    continue

                r.raise_for_status()
                self.pool.report_success(state)
                self._calls += 1
                return self._parse(r.json())

            except (httpx.HTTPError, json.JSONDecodeError) as exc:
                self.pool.report_failure(state, rate_limited=False)
                last_error = exc
                continue

        raise LLMUnavailable(f"all attempts failed: {last_error}")

    @staticmethod
    def _parse(payload: dict) -> Any:
        candidates = payload.get("candidates") or []
        if not candidates:
            raise LLMUnavailable(f"empty response: {str(payload)[:200]}")
        parts = candidates[0].get("content", {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts).strip()
        if not text:
            reason = candidates[0].get("finishReason", "unknown")
            raise LLMUnavailable(f"no text in response (finishReason={reason})")
        return json.loads(text)
