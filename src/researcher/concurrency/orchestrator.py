"""Concurrent research orchestration: fetch all sources in parallel, degrade gracefully.

The orchestrator turns one research query into N per-source fetches that run
concurrently under a configurable semaphore, each protected by the retry +
timeout policy of :class:`~researcher.services.ai_service.AIService` and backed
by the TTL cache. A failing source **never** fails the run: every task resolves
to a :class:`~researcher.models.FetchOutcome`, successful or not, and the
answer is synthesized from whatever succeeded.

A sequential mode is provided solely for the benchmark comparison
(``scripts/bench.py``); production paths always use the parallel mode.
"""

from __future__ import annotations

import asyncio
import logging
import time

from researcher.config import Settings
from researcher.core.query import relaxation_ladder
from researcher.models import FetchOutcome, FetchStatus, SourceName
from researcher.services.ai_service import AIService, AIServiceError
from researcher.services.cache import SourceCache

logger = logging.getLogger(__name__)

#: Order in which sources are queried and reported.
DEFAULT_SOURCES: tuple[SourceName, ...] = (
    SourceName.WIKIPEDIA,
    SourceName.ARXIV,
    SourceName.WEB,
)


class ResearchOrchestrator:
    """Fans one query out to the research sources, bounded and fault-isolated."""

    def __init__(self, settings: Settings, ai_service: AIService, cache: SourceCache) -> None:
        self._settings = settings
        self._ai_service = ai_service
        self._cache = cache
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_requests)

    async def fetch_one(self, query: str, source: SourceName) -> FetchOutcome:
        """Fetch a single source: cache first, then the resilient AI service.

        Never raises — failures come back as ``TIMEOUT``/``ERROR`` outcomes so
        the remaining sources still contribute to the answer.
        """
        started = time.perf_counter()

        cached = await self._cache.get(source, query)
        if cached is not None:
            return FetchOutcome(
                source=source,
                status=FetchStatus.CACHED,
                sources=cached,
                elapsed_seconds=time.perf_counter() - started,
            )

        try:
            async with self._semaphore:
                sources = await self._ai_service.fetch(source, query)
                if not sources:
                    # Query relaxation: title-based search APIs often return
                    # nothing for a full question; retry with keyword forms.
                    for candidate in relaxation_ladder(query):
                        logger.info(
                            "source %s returned no results; retrying with %r",
                            source.value, candidate,
                        )
                        sources = await self._ai_service.fetch(source, candidate)
                        if sources:
                            break
        except AIServiceError as exc:
            status = FetchStatus.TIMEOUT if isinstance(exc.cause, TimeoutError) else FetchStatus.ERROR
            logger.warning("source %s degraded (%s): %s", source.value, status.value, exc)
            return FetchOutcome(
                source=source,
                status=status,
                elapsed_seconds=time.perf_counter() - started,
                error=str(exc),
            )

        # Empty results are not cached: a transient empty answer must not be
        # served for a whole TTL when the source may work on the next attempt.
        if sources:
            await self._cache.set(source, query, sources)
        return FetchOutcome(
            source=source,
            status=FetchStatus.OK,
            sources=sources,
            elapsed_seconds=time.perf_counter() - started,
        )

    async def fetch_all(
        self,
        query: str,
        sources: tuple[SourceName, ...] = DEFAULT_SOURCES,
        *,
        sequential: bool = False,
    ) -> list[FetchOutcome]:
        """Fetch every requested source; parallel by default, ordered results.

        ``asyncio.gather(..., return_exceptions=True)`` is a second line of
        defence: :meth:`fetch_one` already converts expected failures into
        outcomes, but a truly unexpected exception in one task must still not
        cancel its siblings.
        """
        started = time.perf_counter()

        if sequential:
            outcomes: list[FetchOutcome] = [await self.fetch_one(query, s) for s in sources]
        else:
            results = await asyncio.gather(
                *(self.fetch_one(query, s) for s in sources),
                return_exceptions=True,
            )
            outcomes = []
            for source, result in zip(sources, results, strict=True):
                if isinstance(result, BaseException):
                    logger.error("unexpected failure fetching %s: %r", source.value, result)
                    outcomes.append(
                        FetchOutcome(source=source, status=FetchStatus.ERROR, error=repr(result))
                    )
                else:
                    outcomes.append(result)

        elapsed = time.perf_counter() - started
        ok = sum(1 for o in outcomes if o.succeeded)
        logger.info(
            "fetched %d/%d sources in %.2fs (%s): %s",
            ok,
            len(outcomes),
            elapsed,
            "sequential" if sequential else "parallel",
            ", ".join(f"{o.source.value}={o.status.value}" for o in outcomes),
        )
        return outcomes
