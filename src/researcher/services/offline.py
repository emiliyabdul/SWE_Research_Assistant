"""Offline (keyless, network-free) drop-in for :class:`AIService`.

Mirrors the pattern of ``demo_ai.py --offline``: a fake ``LLMProvider`` is
injected through the *public* ``llm=`` parameter of ``ai.synthesize`` — the
``ai/`` package itself is untouched. Source fetches return canned excerpts
after a small simulated latency, so the whole SE stack (cache, semaphore,
orchestration, benchmark) behaves realistically without API keys or network.
"""

from __future__ import annotations

import asyncio
import re

from ai.providers.base import LLMProvider
from ai.schemas import Source
from researcher.config import Settings
from researcher.models import SourceName
from researcher.services.ai_service import AIService

#: Simulated per-fetch latency; keeps the parallel-vs-sequential benchmark meaningful.
SIMULATED_LATENCY_SECONDS = 0.15

_TITLES = {
    SourceName.WIKIPEDIA: "Wikipedia overview",
    SourceName.ARXIV: "arXiv survey",
    SourceName.WEB: "Web article",
}


class OfflineLLM(LLMProvider):
    """Templated synthesizer that cites the sources present in the prompt."""

    def complete(self, prompt: str, *, json_schema=None, max_tokens: int = 1024) -> str:
        n = len(re.findall(r"^\[(\d+)\]", prompt, re.MULTILINE))
        if n == 0:
            return "I cannot answer from the available sources."
        cited = ", ".join(f"[{i}]" for i in range(1, min(n, 3) + 1))
        return (
            f"Based on the available sources, here is a synthesized answer that "
            f"draws from multiple references {cited}. The sources broadly agree "
            f"on the main points; differences in emphasis are noted in each "
            f"reference [1]."
        )


class OfflineAIService(AIService):
    """AIService that fabricates sources and answers locally."""

    def __init__(self, settings: Settings, *, latency: float = SIMULATED_LATENCY_SECONDS) -> None:
        super().__init__(settings, llm=OfflineLLM())
        self._latency = latency

    async def fetch(self, source: SourceName, query: str) -> list[Source]:
        await asyncio.sleep(self._latency)
        return [
            Source(
                title=f"{_TITLES[source]}: {query[:60]}",
                url=f"https://example.org/{source.value}/{abs(hash(query)) % 10_000}",
                snippet=f"Canned {source.value} excerpt about: {query}",
                origin=source.value,
            )
        ]
