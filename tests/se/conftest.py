"""Shared fixtures for the SE-layer test suite (all offline, no network)."""

from __future__ import annotations

import asyncio

import pytest

from ai.schemas import Source
from researcher.config import Settings
from researcher.models import SourceName
from researcher.services.ai_service import AIService, AIServiceError
from researcher.services.cache import SourceCache
from researcher.storage.cache_store import MemoryCacheStore


@pytest.fixture
def settings() -> Settings:
    """Fast-failing settings so retry/timeout tests run in milliseconds."""
    return Settings(
        cache_backend="memory",
        cache_ttl_seconds=60,
        retry_max_attempts=3,
        retry_backoff_base_seconds=0.01,
        retry_backoff_max_seconds=0.02,
        per_source_timeout_seconds=0.25,
        llm_timeout_seconds=0.25,
        max_concurrent_requests=3,
    )


@pytest.fixture
def mem_cache() -> SourceCache:
    return SourceCache(MemoryCacheStore(), ttl_seconds=60)


def make_source(origin: str = "wikipedia", title: str = "T") -> Source:
    return Source(title=title, url=f"https://example.org/{origin}/{title}", snippet="s", origin=origin)


class FakeAIService(AIService):
    """Configurable in-memory stand-in for AIService used by orchestrator tests."""

    def __init__(
        self,
        settings: Settings,
        *,
        delay: float = 0.0,
        fail: set[SourceName] | None = None,
        unexpected: set[SourceName] | None = None,
    ) -> None:
        super().__init__(settings)
        self.delay = delay
        self.fail = fail or set()
        self.unexpected = unexpected or set()
        self.calls: list[SourceName] = []
        self.active = 0
        self.peak = 0

    async def fetch(self, source: SourceName, query: str) -> list[Source]:
        self.calls.append(source)
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if source in self.unexpected:
                raise RuntimeError("unexpected boom")
            if source in self.fail:
                raise AIServiceError(f"fetch[{source.value}]", 3, TimeoutError())
            return [make_source(source.value, f"t-{source.value}")]
        finally:
            self.active -= 1


@pytest.fixture
def fake_ai(settings: Settings) -> FakeAIService:
    return FakeAIService(settings)
