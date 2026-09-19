"""Tests for the core Researcher: validation, happy path, degradation."""

from __future__ import annotations

import pytest

from researcher.concurrency.orchestrator import ResearchOrchestrator
from researcher.config import Settings
from researcher.core.researcher import InvalidQuestionError, Researcher
from researcher.models import SourceName
from researcher.services.ai_service import AIServiceError
from researcher.services.cache import SourceCache
from researcher.services.offline import OfflineAIService
from researcher.storage.cache_store import MemoryCacheStore
from tests.se.conftest import FakeAIService


def build_researcher(settings: Settings, ai_service) -> Researcher:
    cache = SourceCache(MemoryCacheStore(), ttl_seconds=60)
    return Researcher(settings, ResearchOrchestrator(settings, ai_service, cache), ai_service)


# --- validation -------------------------------------------------------------

def test_empty_question_rejected(settings: Settings, fake_ai) -> None:
    researcher = build_researcher(settings, fake_ai)
    with pytest.raises(InvalidQuestionError, match="empty"):
        researcher.validate_question("   ")


def test_oversized_question_rejected(fake_ai) -> None:
    settings = Settings(cache_backend="memory", max_question_length=10)
    researcher = build_researcher(settings, fake_ai)
    with pytest.raises(InvalidQuestionError, match="too long"):
        researcher.validate_question("x" * 11)


def test_control_characters_stripped(settings: Settings, fake_ai) -> None:
    researcher = build_researcher(settings, fake_ai)
    assert researcher.validate_question("what\x00 is\x1b x?\n") == "what is x?"


# --- end-to-end run ----------------------------------------------------------

@pytest.mark.asyncio
async def test_run_happy_path(settings: Settings) -> None:
    researcher = build_researcher(settings, OfflineAIService(settings, latency=0.01))
    result = await researcher.run("What is photosynthesis?")

    assert result.answer is not None
    assert result.answer.citations  # offline LLM cites the sources
    assert len(result.outcomes) == 3
    assert all(o.succeeded for o in result.outcomes)
    assert not result.degraded
    assert result.elapsed_seconds > 0


@pytest.mark.asyncio
async def test_run_degrades_when_one_source_fails(settings: Settings) -> None:
    class PartiallyDown(OfflineAIService):
        async def fetch(self, source, query):
            if source is SourceName.ARXIV:
                raise AIServiceError("fetch[arxiv]", 3, TimeoutError())
            return await super().fetch(source, query)

    researcher = build_researcher(settings, PartiallyDown(settings, latency=0.01))
    result = await researcher.run("q")

    assert result.answer is not None
    assert result.degraded
    failed = [o for o in result.outcomes if not o.succeeded]
    assert [o.source for o in failed] == [SourceName.ARXIV]


@pytest.mark.asyncio
async def test_run_no_sources_yields_no_answer(settings: Settings) -> None:
    ai = FakeAIService(
        settings,
        fail={SourceName.WIKIPEDIA, SourceName.ARXIV, SourceName.WEB},
    )
    researcher = build_researcher(settings, ai)
    result = await researcher.run("q")

    assert result.answer is None
    assert all(not o.succeeded for o in result.outcomes)


@pytest.mark.asyncio
async def test_run_synthesis_failure_returns_sources_without_answer(settings: Settings) -> None:
    class SynthDown(OfflineAIService):
        async def synthesize(self, question, sources, *, llm=None):
            raise AIServiceError("synthesize", 3, TimeoutError())

    researcher = build_researcher(settings, SynthDown(settings, latency=0.01))
    result = await researcher.run("q")

    assert result.answer is None
    assert all(o.succeeded for o in result.outcomes)


@pytest.mark.asyncio
async def test_run_rejects_invalid_question(settings: Settings, fake_ai) -> None:
    researcher = build_researcher(settings, fake_ai)
    with pytest.raises(InvalidQuestionError):
        await researcher.run("")
