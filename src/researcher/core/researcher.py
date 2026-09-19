"""Core business logic: run one research question end to end.

The :class:`Researcher` knows *what* to do (validate → gather sources →
synthesize → package the result) and delegates *how* to the injected
collaborators. It never talks to ``ai.*`` directly — everything crosses the
AI boundary through :class:`~researcher.services.ai_service.AIService`.
"""

from __future__ import annotations

import logging
import re
import time

from researcher.concurrency.orchestrator import DEFAULT_SOURCES, ResearchOrchestrator
from researcher.config import Settings
from researcher.models import ResearchResult, SourceName
from researcher.services.ai_service import AIService, AIServiceError

logger = logging.getLogger(__name__)

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class InvalidQuestionError(ValueError):
    """Raised when a research question fails validation."""


class Researcher:
    """Answers one research question using the orchestrator and the AI service."""

    def __init__(
        self,
        settings: Settings,
        orchestrator: ResearchOrchestrator,
        ai_service: AIService,
    ) -> None:
        self._settings = settings
        self._orchestrator = orchestrator
        self._ai_service = ai_service

    def validate_question(self, question: str) -> str:
        """Sanitise and validate user input; returns the cleaned question.

        Raises :class:`InvalidQuestionError` with a user-presentable message —
        bad input must produce a clean error, never a stack trace.
        """
        cleaned = _CONTROL_CHARS.sub("", question).strip()
        if not cleaned:
            raise InvalidQuestionError("question must not be empty")
        limit = self._settings.max_question_length
        if len(cleaned) > limit:
            raise InvalidQuestionError(
                f"question is too long ({len(cleaned)} chars; maximum is {limit})"
            )
        return cleaned

    async def run(
        self,
        question: str,
        sources: tuple[SourceName, ...] = DEFAULT_SOURCES,
        *,
        sequential: bool = False,
    ) -> ResearchResult:
        """Research *question*: fetch sources (parallel by default) and synthesize.

        Degrades gracefully at every stage: failed sources are reported in the
        outcomes, and a failed synthesis yields a result with ``answer=None``
        rather than an exception.
        """
        cleaned = self.validate_question(question)
        started = time.perf_counter()

        outcomes = await self._orchestrator.fetch_all(cleaned, sources, sequential=sequential)
        collected = [s for outcome in outcomes for s in outcome.sources]

        answer = None
        if collected:
            try:
                answer = await self._ai_service.synthesize(cleaned, collected)
            except AIServiceError as exc:
                logger.error("synthesis failed, returning sources without answer: %s", exc)
        else:
            logger.warning("no sources retrieved for %r; skipping synthesis", cleaned)

        result = ResearchResult(
            question=cleaned,
            outcomes=outcomes,
            answer=answer,
            elapsed_seconds=time.perf_counter() - started,
        )
        logger.info(
            "research complete in %.2fs: %d/%d sources, answer=%s%s",
            result.elapsed_seconds,
            sum(1 for o in outcomes if o.succeeded),
            len(outcomes),
            "yes" if answer else "no",
            " (degraded)" if result.degraded else "",
        )
        return result
