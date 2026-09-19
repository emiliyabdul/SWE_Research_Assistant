"""Tests for the OpenRouter LLM adapter (offline: httpx.MockTransport)."""

from __future__ import annotations

import json

import httpx
import pytest

from ai.providers.base import LLMProvider, ProviderError
from researcher.services.openrouter import (
    OPENROUTER_MODELS,
    OpenRouterLLM,
    create_openrouter_llm,
    openrouter_available,
)

MODEL = next(iter(OPENROUTER_MODELS))


def transport_returning(payload: dict, status_code: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=payload, request=request)

    return httpx.MockTransport(handler)


def test_is_an_llm_provider() -> None:
    assert issubclass(OpenRouterLLM, LLMProvider)


def test_unknown_model_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown OpenRouter model"):
        OpenRouterLLM("not/a-model")


def test_factory_builds_known_model() -> None:
    llm = create_openrouter_llm(MODEL)
    assert llm.model == MODEL


def test_missing_key_raises_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    llm = OpenRouterLLM(MODEL)
    with pytest.raises(ProviderError, match="OPENROUTER_API_KEY"):
        llm.complete("hi")
    assert not openrouter_available()


def test_available_when_key_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    assert openrouter_available()


def test_complete_returns_content() -> None:
    payload = {"choices": [{"message": {"content": "  an answer  "}}]}
    llm = OpenRouterLLM(MODEL, api_key="test-key", transport=transport_returning(payload))
    assert llm.complete("question?") == "an answer"


def test_complete_sends_schema_and_json_mode() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "{}"}}]}, request=request
        )

    llm = OpenRouterLLM(MODEL, api_key="test-key", transport=httpx.MockTransport(handler))
    llm.complete("q", json_schema={"type": "object"}, max_tokens=2048)

    assert seen["auth"] == "Bearer test-key"
    assert seen["body"]["model"] == MODEL
    assert seen["body"]["max_tokens"] == 2048
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert "valid JSON matching this schema" in seen["body"]["messages"][0]["content"]


def test_http_error_becomes_provider_error() -> None:
    llm = OpenRouterLLM(
        MODEL, api_key="test-key", transport=transport_returning({"error": "nope"}, 429)
    )
    with pytest.raises(ProviderError, match="OpenRouter call failed"):
        llm.complete("q")


def test_unexpected_shape_becomes_provider_error() -> None:
    llm = OpenRouterLLM(MODEL, api_key="test-key", transport=transport_returning({"choices": []}))
    with pytest.raises(ProviderError, match="unexpected shape"):
        llm.complete("q")
