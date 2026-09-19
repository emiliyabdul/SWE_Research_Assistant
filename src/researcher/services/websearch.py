"""SE-layer web-search adapter.

The provided ``ai.sources.DuckDuckGoProvider`` depends on the legacy
``duckduckgo-search`` library, which has been renamed to ``ddgs`` and no longer
returns results. ``ai.fetch_web`` accepts any :class:`ai.sources.WebSearchProvider`
through its public ``provider=`` parameter, so we supply a working adapter here
— the ``ai/`` package itself stays untouched (same injection pattern as the
offline LLM).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ai.schemas import Source
from ai.sources import WebSearchProvider
from researcher.config import Settings

logger = logging.getLogger(__name__)


class DdgsWebSearchProvider(WebSearchProvider):
    """DuckDuckGo search via the maintained ``ddgs`` package. No API key."""

    async def search(
        self,
        query: str,
        *,
        max_results: int = 3,
        client: Any = None,
    ) -> list[Source]:
        # `client` is unused — the ddgs library manages its own HTTP.
        def _run() -> list[Source]:
            from ddgs import DDGS

            results: list[Source] = []
            with DDGS() as ddgs:
                for item in ddgs.text(query, max_results=max_results):
                    if not item.get("href"):
                        continue
                    results.append(
                        Source(
                            title=item.get("title", "(untitled)"),
                            url=item["href"],
                            snippet=item.get("body", ""),
                            origin="web",
                        )
                    )
            return results

        return await asyncio.to_thread(_run)


def create_web_search_provider(settings: Settings) -> WebSearchProvider | None:
    """Return our ddgs adapter when DuckDuckGo is configured, else ``None``.

    ``None`` lets ``ai.fetch_web`` fall back to its own factory (Tavily /
    Serper), which work fine — only the DuckDuckGo path needs replacing.
    """
    if settings.web_search_provider == "duckduckgo":
        try:
            import ddgs  # noqa: F401
        except ImportError:
            logger.warning("ddgs package not installed; falling back to ai's own provider")
            return None
        return DdgsWebSearchProvider()
    return None
