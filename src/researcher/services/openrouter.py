"""OpenRouter LLM adapter — an SE-layer :class:`~ai.providers.base.LLMProvider`.

OpenRouter exposes an OpenAI-compatible ``/chat/completions`` endpoint that
fronts many open-weight models. This module adds a *per-request* model choice
on top of the provided ``ai`` package without modifying it: the adapter
subclasses the same abstract ``LLMProvider`` that ``ai.synthesize`` expects
and is injected through :meth:`AIService.synthesize`'s public ``llm``
parameter (our own inheritance example, mirroring the ABC pattern in
``ai/providers``).

The HTTP call is synchronous on purpose: ``AIService`` already runs
``ai.synthesize`` in a worker thread (``asyncio.to_thread``) and bounds it
with ``llm_timeout_seconds``, so this adapter stays a plain blocking client
like the providers in ``ai/``.
"""

from __future__ import annotations

import json
import logging
import os

import httpx

from ai.providers.base import LLMProvider, ProviderError

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

#: Models offered in the UI dropdown and ``--model`` CLI flag, id -> label.
OPENROUTER_MODELS: dict[str, str] = {
    "qwen/qwen3.8-27b:free": "Qwen3.8 27B (free)",
    "deepseek/deepseek-v4-flash-0731:free": "DeepSeek V4 Flash (free)",
}


def openrouter_available() -> bool:
    """True when an OpenRouter key is configured in the environment."""
    return bool(os.getenv("OPENROUTER_API_KEY"))


class OpenRouterLLM(LLMProvider):
    """OpenAI-compatible chat completions against OpenRouter.

    Reads ``OPENROUTER_API_KEY`` from the environment lazily (mirroring the
    ``ai/`` providers), so constructing the adapter never requires a key —
    only calling it does. A custom ``transport`` can be injected for fully
    offline tests.
    """

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 120.0,
    ) -> None:
        if model not in OPENROUTER_MODELS:
            raise ValueError(
                f"Unknown OpenRouter model {model!r}; expected one of "
                f"{sorted(OPENROUTER_MODELS)}"
            )
        self.model = model
        self._api_key = api_key
        self._transport = transport
        self._timeout = timeout

    def complete(
        self,
        prompt: str,
        *,
        json_schema: dict | None = None,
        max_tokens: int = 1024,
    ) -> str:
        key = self._api_key or os.getenv("OPENROUTER_API_KEY")
        if not key:
            raise ProviderError("OPENROUTER_API_KEY is not set.")

        full_prompt = prompt
        if json_schema is not None:
            # Same contract as the providers in ai/: schema appended to the
            # prompt plus the provider's JSON response mode.
            full_prompt = (
                prompt
                + "\n\nReturn ONLY valid JSON matching this schema "
                "(no prose, no markdown fences):\n"
                + json.dumps(json_schema, indent=2)
            )

        payload: dict = {
            "model": self.model,
            "messages": [{"role": "user", "content": full_prompt}],
            "max_tokens": max_tokens,
        }
        if json_schema is not None:
            payload["response_format"] = {"type": "json_object"}

        try:
            with httpx.Client(
                base_url=OPENROUTER_BASE_URL,
                headers={"Authorization": f"Bearer {key}"},
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                resp = client.post("/chat/completions", json=payload)
                resp.raise_for_status()
                body = resp.json()
        except httpx.HTTPError as exc:
            raise ProviderError(f"OpenRouter call failed: {exc}") from exc

        try:
            content = body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"OpenRouter returned an unexpected shape: {body!r}") from exc

        logger.debug("openrouter complete model=%s tokens<=%d", self.model, max_tokens)
        return content.strip()


def create_openrouter_llm(model: str) -> OpenRouterLLM:
    """Validated factory used by the CLI and the web backend."""
    return OpenRouterLLM(model)
