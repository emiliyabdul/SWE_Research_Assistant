"""Tests for the resilient AIService: retries, timeouts, error semantics.

The provided ``ai`` module functions are monkeypatched at the module attribute
level — the ``ai/`` package itself is never modified.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from ai.schemas import AnswerWithCitations
from researcher.config import Settings
from researcher.models import SourceName
from researcher.services.ai_service import AIService, AIServiceError
from tests.se.conftest import make_source


@pytest.mark.asyncio
async def test_fetch_success_first_try(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    sources = [make_source("wikipedia")]

    async def ok(query, *, max_results=3, client=None):
        return sources

    monkeypatch.setattr("ai.fetch_wikipedia", ok)
    assert await AIService(settings).fetch(SourceName.WIKIPEDIA, "q") == sources


@pytest.mark.asyncio
async def test_fetch_retries_transient_then_succeeds(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    async def flaky(query, *, max_results=3, client=None):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise httpx.ConnectError("boom")
        return [make_source("arxiv")]

    monkeypatch.setattr("ai.fetch_arxiv", flaky)
    result = await AIService(settings).fetch(SourceName.ARXIV, "q")
    assert calls == 3
    assert result[0].origin == "arxiv"


@pytest.mark.asyncio
async def test_fetch_gives_up_after_max_attempts(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    async def dead(query, *, max_results=3, client=None):
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("down")

    monkeypatch.setattr("ai.fetch_web", dead)
    with pytest.raises(AIServiceError) as excinfo:
        await AIService(settings).fetch(SourceName.WEB, "q")
    assert calls == settings.retry_max_attempts
    assert excinfo.value.attempts == settings.retry_max_attempts
    assert excinfo.value.operation == "fetch[web]"
    assert isinstance(excinfo.value.cause, httpx.ConnectError)


@pytest.mark.asyncio
async def test_fetch_per_attempt_timeout(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    async def slow(query, *, max_results=3, client=None):
        await asyncio.sleep(5)

    monkeypatch.setattr("ai.fetch_wikipedia", slow)
    with pytest.raises(AIServiceError) as excinfo:
        await AIService(settings).fetch(SourceName.WIKIPEDIA, "q")
    assert isinstance(excinfo.value.cause, TimeoutError)


@pytest.mark.asyncio
async def test_non_retryable_error_fails_fast(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    async def invalid(query, *, max_results=3, client=None):
        nonlocal calls
        calls += 1
        raise ValueError("bad input")

    monkeypatch.setattr("ai.fetch_arxiv", invalid)
    with pytest.raises(ValueError):
        await AIService(settings).fetch(SourceName.ARXIV, "q")
    assert calls == 1  # no retries on programmer/user errors


@pytest.mark.asyncio
async def test_synthesize_runs_blocking_call_in_thread(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_synth(question, sources, *, llm=None):
        return AnswerWithCitations(question=question, answer="A [1]")

    monkeypatch.setattr("ai.synthesize", fake_synth)
    answer = await AIService(settings).synthesize("q?", [make_source()])
    assert answer.answer == "A [1]"


@pytest.mark.asyncio
async def test_synthesize_retries_provider_error(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai.providers.base import ProviderError

    calls = 0

    def flaky_synth(question, sources, *, llm=None):
        nonlocal calls
        calls += 1
        if calls < 2:
            raise ProviderError("rate limited")
        return AnswerWithCitations(question=question, answer="ok [1]")

    monkeypatch.setattr("ai.synthesize", flaky_synth)
    answer = await AIService(settings).synthesize("q?", [make_source()])
    assert calls == 2
    assert answer.answer == "ok [1]"


def test_token_budget_wrapper_raises_the_budget() -> None:
    from researcher.services.ai_service import TokenBudgetLLM

    class Recorder:
        def complete(self, prompt, *, json_schema=None, max_tokens=1024):
            self.max_tokens = max_tokens
            return "answer [1]"

    inner = Recorder()
    wrapper = TokenBudgetLLM(4096, inner=inner)  # type: ignore[arg-type]
    assert wrapper.complete("p") == "answer [1]"
    assert inner.max_tokens == 4096

    # an explicit larger request wins over the configured budget
    wrapper.complete("p", max_tokens=9000)
    assert inner.max_tokens == 9000


@pytest.mark.asyncio
async def test_synthesize_injects_budgeted_llm(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from researcher.services.ai_service import TokenBudgetLLM

    seen: dict = {}

    def capture_synth(question, sources, *, llm=None):
        seen["llm"] = llm
        return AnswerWithCitations(question=question, answer="a [1]")

    monkeypatch.setattr("ai.synthesize", capture_synth)
    await AIService(settings).synthesize("q?", [make_source()])
    assert isinstance(seen["llm"], TokenBudgetLLM)
