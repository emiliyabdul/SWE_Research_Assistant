"""Command-line interface: ``python -m researcher ask "your question"``.

This module is also the composition root: it builds the settings, cache store,
shared ``httpx.AsyncClient``, AI service (live or offline), orchestrator and
researcher, then renders the result.

Exit codes: 0 = answer produced (possibly degraded) · 1 = no answer could be
produced · 2 = invalid input.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys

import click
import httpx
from dotenv import load_dotenv

from researcher.concurrency.orchestrator import DEFAULT_SOURCES, ResearchOrchestrator
from researcher.config import Settings, get_settings
from researcher.core.researcher import InvalidQuestionError, Researcher
from researcher.models import ResearchResult, SourceName
from researcher.services.ai_service import AIService
from researcher.services.cache import SourceCache
from researcher.services.offline import OfflineAIService
from researcher.services.websearch import create_web_search_provider
from researcher.storage.cache_store import create_cache_store

#: Headers for the shared HTTP client: Wikimedia's robot policy (https://w.wiki/4wJS)
#: rejects non-browser clients whose User-Agent has no contact address.
HTTP_HEADERS = {
    "User-Agent": "researcher/0.1 (AI-ENG-110 course project; mailto:researcher@example.com) httpx"
}


def make_http_client(timeout: float) -> httpx.AsyncClient:
    """Shared AsyncClient for all source fetchers.

    ``follow_redirects`` is required because arXiv's API 301-redirects
    http -> https; the descriptive User-Agent is required by Wikipedia.
    """
    return httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=HTTP_HEADERS)


def setup_logging(level: str) -> None:
    """Configure structured logging to stderr (never pollutes stdout output)."""
    logging.basicConfig(
        level=level,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def parse_sources(spec: str | None) -> tuple[SourceName, ...]:
    """Parse the ``--sources`` flag (e.g. ``wiki,arxiv``) into source names."""
    if not spec:
        return DEFAULT_SOURCES
    names = [part for part in (p.strip() for p in spec.split(",")) if part]
    if not names:
        raise InvalidQuestionError("--sources must name at least one source")
    parsed: list[SourceName] = []
    for name in names:
        source = SourceName.parse(name)  # raises ValueError with valid options
        if source not in parsed:
            parsed.append(source)
    return tuple(parsed)


def result_to_dict(result: ResearchResult) -> dict:
    """JSON-safe shape of a :class:`ResearchResult` for ``ask --json``.

    The optional web UI (``ui/server.py``) renders a richer view of the same
    result and keeps its own top-level shape, but delegates the per-outcome
    fields to :meth:`FetchOutcome.to_dict`, so that part isn't duplicated.
    """
    return {
        "question": result.question,
        "answer": result.answer.to_dict() if result.answer else None,
        "sources": [o.to_dict() for o in result.outcomes],
        "elapsed_seconds": round(result.elapsed_seconds, 3),
        "degraded": result.degraded,
    }


def render_result(result: ResearchResult) -> str:
    """Human-readable rendering: answer, numbered references, degradation notes."""
    lines = [f"Q: {result.question}", ""]
    if result.answer is None:
        retrieved = sum(1 for o in result.outcomes if o.succeeded)
        if retrieved:
            lines.append(
                f"No answer could be produced (synthesis failed; {retrieved} source(s) were retrieved)."
            )
        else:
            lines.append("No answer could be produced (no sources were retrieved).")
    else:
        lines += [f"A: {result.answer.answer}", ""]
        if result.answer.citations:
            lines.append("References:")
            for c in result.answer.citations:
                lines.append(f"  [{c.index}] ({c.source.origin}) {c.source.title}")
                lines.append(f"      {c.source.url}")
    failed = [o for o in result.outcomes if not o.succeeded]
    if failed:
        lines.append("")
        for o in failed:
            lines.append(f"Note: {o.source.value} unavailable ({o.status.value}); answer based on remaining sources.")
    lines.append("")
    lines.append(
        f"({sum(1 for o in result.outcomes if o.succeeded)}/{len(result.outcomes)} sources, "
        f"{result.elapsed_seconds:.2f}s"
        + (", cached" if all(o.status.value == "cached" for o in result.outcomes) else "")
        + ")"
    )
    return "\n".join(lines)


async def run_ask(
    settings: Settings,
    question: str,
    sources: tuple[SourceName, ...],
    *,
    use_cache: bool,
    sequential: bool,
    offline: bool,
) -> ResearchResult:
    """Wire the object graph and run one research question."""
    cache = SourceCache(
        create_cache_store(settings),
        ttl_seconds=settings.cache_ttl_seconds,
        enabled=use_cache,
        namespace="offline" if offline else "",
    )
    try:
        if offline:
            ai_service: AIService = OfflineAIService(settings)
            researcher = Researcher(settings, ResearchOrchestrator(settings, ai_service, cache), ai_service)
            return await researcher.run(question, sources, sequential=sequential)
        async with make_http_client(settings.per_source_timeout_seconds) as client:
            ai_service = AIService(
                settings,
                client=client,
                web_provider=create_web_search_provider(settings),
            )
            researcher = Researcher(settings, ResearchOrchestrator(settings, ai_service, cache), ai_service)
            return await researcher.run(question, sources, sequential=sequential)
    finally:
        await cache.close()


@click.group()
def cli() -> None:
    """Async Research Assistant — ask a question, get a cited answer."""
    # The provided ai/ package reads its env vars (provider selection, API
    # keys) from os.environ directly; pydantic-settings reads .env only into
    # our Settings object. Export .env to the process env so both see it.
    load_dotenv()


@cli.command()
@click.argument("question")
@click.option("--sources", "sources_spec", default=None, metavar="LIST",
              help="Comma-separated subset of sources: wiki,arxiv,web (default: all).")
@click.option("--no-cache", is_flag=True, help="Bypass the TTL cache for this run.")
@click.option("--sequential", is_flag=True, help="Fetch sources one at a time (benchmark aid).")
@click.option("--offline", is_flag=True, help="Run without API keys or network (canned sources, fake LLM).")
@click.option("--json", "as_json", is_flag=True, help="Emit the result as JSON on stdout.")
def ask(question: str, sources_spec: str | None, no_cache: bool, sequential: bool,
        offline: bool, as_json: bool) -> None:
    """Research QUESTION across Wikipedia, arXiv and the web, with citations."""
    settings = get_settings()
    setup_logging(settings.log_level.value)

    try:
        sources = parse_sources(sources_spec)
    except (InvalidQuestionError, ValueError) as exc:
        raise click.UsageError(str(exc)) from exc

    try:
        result = asyncio.run(
            run_ask(settings, question, sources,
                    use_cache=not no_cache, sequential=sequential, offline=offline)
        )
    except InvalidQuestionError as exc:
        raise click.UsageError(str(exc)) from exc

    if as_json:
        click.echo(json.dumps(result_to_dict(result), indent=2))
    else:
        click.echo(render_result(result))

    if result.answer is None:
        sys.exit(1)


if __name__ == "__main__":
    cli()
