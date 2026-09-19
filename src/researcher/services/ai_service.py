"""Resilient wrapper around every call into the provided ``ai/`` package.

This is the only module in the SE layer allowed to import ``ai`` call sites:
business logic goes through :class:`AIService`, which adds — per the project
brief — retries with exponential backoff, per-attempt timeouts, and structured
logging. The ``ai/`` package itself is never modified.

Design notes
------------
* The source fetchers (``ai.fetch_wikipedia`` / ``fetch_arxiv`` / ``fetch_web``)
  are coroutines; each attempt is bounded by ``asyncio.timeout``.
* ``ai.synthesize`` is synchronous (blocking SDK call), so it runs in a worker
  thread via ``asyncio.to_thread`` under the same timeout discipline.
* Retries fire only on transient failures (network errors, provider errors,
  attempt timeouts) — never on validation errors, which are programmer/user
  mistakes and must surface immediately.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

import httpx
from tenacity import (
    AsyncRetrying,
    RetryError,
    before_sleep_log,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

import ai
from ai.providers.base import LLMProvider, ProviderError
from ai.providers.factory import get_llm
from ai.schemas import AnswerWithCitations, Source
from ai.sources import WebSearchProvider
from researcher.config import Settings
from researcher.models import SourceName

logger = logging.getLogger(__name__)

#: Exception types worth retrying — transient by nature.
RETRYABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    httpx.HTTPError,
    ProviderError,
    TimeoutError,
)


class AIServiceError(Exception):
    """Raised when an AI call keeps failing after all retry attempts."""

    def __init__(self, operation: str, attempts: int, cause: BaseException) -> None:
        super().__init__(f"{operation} failed after {attempts} attempt(s): {cause!r}")
        self.operation = operation
        self.attempts = attempts
        self.cause = cause


class TokenBudgetLLM(LLMProvider):
    """Raises the completion token budget of the configured provider.

    ``ai.synthesize`` calls ``llm.complete(prompt)`` with the provider default
    of 1024 tokens; reasoning models spend part of that budget on internal
    thinking, which can truncate the visible answer mid-sentence. This wrapper
    forwards a configurable budget through the provider's public interface.
    The inner provider is created lazily (on first use) so constructing the
    wrapper never requires API keys.
    """

    def __init__(self, max_tokens: int, inner: LLMProvider | None = None) -> None:
        self._max_tokens = max_tokens
        self._inner = inner

    def complete(self, prompt: str, *, json_schema: dict | None = None,
                 max_tokens: int = 1024) -> str:
        if self._inner is None:
            self._inner = get_llm()
        return self._inner.complete(
            prompt, json_schema=json_schema, max_tokens=max(max_tokens, self._max_tokens)
        )


class AIService:
    """Retrying, timeout-bounded, logged facade over the ``ai`` package."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        web_provider: WebSearchProvider | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._web_provider = web_provider

    def _retrying(self) -> AsyncRetrying:
        s = self._settings
        return AsyncRetrying(
            stop=stop_after_attempt(s.retry_max_attempts),
            wait=wait_exponential(
                multiplier=s.retry_backoff_base_seconds,
                max=s.retry_backoff_max_seconds,
            ),
            retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=False,
        )

    async def _call_with_policy(
        self,
        operation: str,
        timeout_seconds: float,
        call: Callable[[], Awaitable[object]],
    ) -> object:
        """Run *call* under the retry + per-attempt-timeout policy."""
        started = time.perf_counter()
        attempt_no = 0
        try:
            async for attempt in self._retrying():
                with attempt:
                    attempt_no = attempt.retry_state.attempt_number
                    logger.debug("%s: attempt %d starting", operation, attempt_no)
                    async with asyncio.timeout(timeout_seconds):
                        result = await call()
                if not attempt.retry_state.outcome.failed:  # type: ignore[union-attr]
                    attempt.retry_state.set_result(result)
        except RetryError as exc:
            cause = exc.last_attempt.exception()
            assert cause is not None
            logger.error(
                "%s: giving up after %d attempt(s) in %.2fs: %r",
                operation, attempt_no, time.perf_counter() - started, cause,
            )
            raise AIServiceError(operation, attempt_no, cause) from cause
        elapsed = time.perf_counter() - started
        logger.info("%s: ok in %.2fs (attempts=%d)", operation, elapsed, attempt_no)
        return result

    async def fetch(self, source: SourceName, query: str) -> list[Source]:
        """Fetch excerpts for *query* from one source, resiliently."""
        s = self._settings
        fetchers: dict[SourceName, Callable[..., Awaitable[list[Source]]]] = {
            SourceName.WIKIPEDIA: ai.fetch_wikipedia,
            SourceName.ARXIV: ai.fetch_arxiv,
            SourceName.WEB: ai.fetch_web,
        }
        fetcher = fetchers[source]

        async def call() -> list[Source]:
            if source is SourceName.WEB and self._web_provider is not None:
                return await ai.fetch_web(
                    query,
                    max_results=s.max_sources_per_query,
                    provider=self._web_provider,
                    client=self._client,
                )
            return await fetcher(
                query,
                max_results=s.max_sources_per_query,
                client=self._client,
            )

        result = await self._call_with_policy(
            f"fetch[{source.value}]", s.per_source_timeout_seconds, call
        )
        return result  # type: ignore[return-value]

    async def synthesize(
        self,
        question: str,
        sources: list[Source],
        *,
        llm: LLMProvider | None = None,
    ) -> AnswerWithCitations:
        """Synthesize a cited answer; the blocking LLM call runs in a thread."""

        async def call() -> AnswerWithCitations:
            provider = llm if llm is not None else TokenBudgetLLM(self._settings.llm_max_tokens)
            return await asyncio.to_thread(ai.synthesize, question, sources, llm=provider)

        result = await self._call_with_policy(
            "synthesize", self._settings.llm_timeout_seconds, call
        )
        return result  # type: ignore[return-value]
