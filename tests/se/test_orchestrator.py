"""Tests for the concurrent orchestrator: parallelism, degradation, caching."""

from __future__ import annotations

import time

import pytest

from researcher.concurrency.orchestrator import DEFAULT_SOURCES, ResearchOrchestrator
from researcher.config import Settings
from researcher.models import FetchStatus, SourceName
from researcher.services.cache import SourceCache
from researcher.storage.cache_store import MemoryCacheStore
from tests.se.conftest import FakeAIService, make_source


def fresh_cache() -> SourceCache:
    return SourceCache(MemoryCacheStore(), ttl_seconds=60)


@pytest.mark.asyncio
async def test_parallel_is_faster_than_sequential(settings: Settings) -> None:
    delay = 0.15

    started = time.perf_counter()
    outcomes = await ResearchOrchestrator(
        settings, FakeAIService(settings, delay=delay), fresh_cache()
    ).fetch_all("q")
    parallel = time.perf_counter() - started

    started = time.perf_counter()
    await ResearchOrchestrator(
        settings, FakeAIService(settings, delay=delay), fresh_cache()
    ).fetch_all("q", sequential=True)
    sequential = time.perf_counter() - started

    assert all(o.status is FetchStatus.OK for o in outcomes)
    assert [o.source for o in outcomes] == list(DEFAULT_SOURCES)
    assert parallel < delay * 2, f"parallel too slow: {parallel:.2f}s"
    assert sequential > delay * 2.5, f"sequential too fast: {sequential:.2f}s"


@pytest.mark.asyncio
async def test_one_failed_source_degrades_gracefully(settings: Settings) -> None:
    ai = FakeAIService(settings, delay=0.01, fail={SourceName.ARXIV})
    outcomes = await ResearchOrchestrator(settings, ai, fresh_cache()).fetch_all("q")
    by_source = {o.source: o for o in outcomes}

    assert by_source[SourceName.ARXIV].status is FetchStatus.TIMEOUT
    assert by_source[SourceName.ARXIV].error
    assert by_source[SourceName.WIKIPEDIA].succeeded
    assert by_source[SourceName.WEB].succeeded


@pytest.mark.asyncio
async def test_unexpected_exception_is_isolated(settings: Settings) -> None:
    ai = FakeAIService(settings, delay=0.01, unexpected={SourceName.WEB})
    outcomes = await ResearchOrchestrator(settings, ai, fresh_cache()).fetch_all("q")
    by_source = {o.source: o for o in outcomes}

    assert by_source[SourceName.WEB].status is FetchStatus.ERROR
    assert "unexpected" in (by_source[SourceName.WEB].error or "")
    assert by_source[SourceName.WIKIPEDIA].succeeded


@pytest.mark.asyncio
async def test_warm_cache_serves_all_sources(settings: Settings) -> None:
    ai = FakeAIService(settings, delay=0.01)
    orchestrator = ResearchOrchestrator(settings, ai, fresh_cache())

    await orchestrator.fetch_all("What is X?")
    fetch_count = len(ai.calls)

    outcomes = await orchestrator.fetch_all("what is x")  # equivalent phrasing
    assert len(ai.calls) == fetch_count  # zero new fetches
    assert all(o.status is FetchStatus.CACHED for o in outcomes)


@pytest.mark.asyncio
async def test_failures_are_not_cached(settings: Settings) -> None:
    ai = FakeAIService(settings, delay=0.01, fail={SourceName.ARXIV})
    orchestrator = ResearchOrchestrator(settings, ai, fresh_cache())
    await orchestrator.fetch_all("q")

    ai.fail = set()  # source recovers
    outcomes = await orchestrator.fetch_all("q")
    by_source = {o.source: o for o in outcomes}
    assert by_source[SourceName.ARXIV].status is FetchStatus.OK


@pytest.mark.asyncio
@pytest.mark.parametrize("bound", [1, 3])
async def test_semaphore_bounds_concurrency(bound: int) -> None:
    settings = Settings(cache_backend="memory", max_concurrent_requests=bound)
    ai = FakeAIService(settings, delay=0.05)
    await ResearchOrchestrator(settings, ai, fresh_cache()).fetch_all("q")
    assert ai.peak == bound


@pytest.mark.asyncio
async def test_source_subset_respected(settings: Settings) -> None:
    ai = FakeAIService(settings)
    outcomes = await ResearchOrchestrator(settings, ai, fresh_cache()).fetch_all(
        "q", (SourceName.WIKIPEDIA, SourceName.ARXIV)
    )
    assert [o.source for o in outcomes] == [SourceName.WIKIPEDIA, SourceName.ARXIV]
    assert set(ai.calls) == {SourceName.WIKIPEDIA, SourceName.ARXIV}


class EmptyUnlessKeywords(FakeAIService):
    """Returns results only for short keyword queries (mimics Wikipedia search)."""

    def __init__(self, settings: Settings, accept: str) -> None:
        super().__init__(settings)
        self.accept = accept
        self.queries: list[str] = []

    async def fetch(self, source: SourceName, query: str):  # type: ignore[override]
        self.queries.append(query)
        await super().fetch(source, query)
        return [make_source(source.value)] if query == self.accept else []


@pytest.mark.asyncio
async def test_empty_results_trigger_query_relaxation(settings: Settings) -> None:
    ai = EmptyUnlessKeywords(settings, accept="fusion energy")
    orchestrator = ResearchOrchestrator(settings, ai, fresh_cache())
    outcomes = await orchestrator.fetch_all(
        "What is the current state of fusion energy research?", (SourceName.WIKIPEDIA,)
    )
    assert outcomes[0].status is FetchStatus.OK
    assert outcomes[0].sources  # found via the relaxed query
    assert "fusion energy research" in ai.queries  # first rung tried
    assert "fusion energy" in ai.queries  # second rung succeeded


@pytest.mark.asyncio
async def test_empty_results_are_not_cached(settings: Settings) -> None:
    class AlwaysEmpty(FakeAIService):
        async def fetch(self, source: SourceName, query: str):  # type: ignore[override]
            await super().fetch(source, query)
            return []

    ai = AlwaysEmpty(settings)
    cache = fresh_cache()
    orchestrator = ResearchOrchestrator(settings, ai, cache)
    await orchestrator.fetch_all("some question", (SourceName.WEB,))
    calls_before = len(ai.calls)
    outcomes = await orchestrator.fetch_all("some question", (SourceName.WEB,))
    assert len(ai.calls) > calls_before  # second run fetched again, no cache hit
    assert outcomes[0].status is FetchStatus.OK

