"""Tests for the SE-layer domain models."""

from __future__ import annotations

import time

import pytest

from ai.schemas import AnswerWithCitations
from researcher.models import (
    CacheEntry,
    FetchOutcome,
    FetchStatus,
    ResearchResult,
    SourceName,
)
from tests.se.conftest import make_source


@pytest.mark.parametrize(
    ("alias", "expected"),
    [
        ("wiki", SourceName.WIKIPEDIA),
        ("Wikipedia", SourceName.WIKIPEDIA),
        (" ARXIV ", SourceName.ARXIV),
        ("web", SourceName.WEB),
    ],
)
def test_source_name_parse_aliases(alias: str, expected: SourceName) -> None:
    assert SourceName.parse(alias) is expected


def test_source_name_parse_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="unknown source"):
        SourceName.parse("bing")


def test_fetch_outcome_succeeded() -> None:
    ok = FetchOutcome(source=SourceName.WEB, status=FetchStatus.OK)
    cached = FetchOutcome(source=SourceName.WEB, status=FetchStatus.CACHED)
    bad = FetchOutcome(source=SourceName.WEB, status=FetchStatus.ERROR, error="x")
    timeout = FetchOutcome(source=SourceName.WEB, status=FetchStatus.TIMEOUT, error="t")
    assert ok.succeeded and cached.succeeded
    assert not bad.succeeded and not timeout.succeeded


def test_cache_entry_expiry() -> None:
    live = CacheEntry(source=SourceName.WEB, query="q", sources=[], expires_at=time.time() + 60)
    dead = CacheEntry(source=SourceName.WEB, query="q", sources=[], expires_at=time.time() - 1)
    assert not live.is_expired
    assert dead.is_expired


def test_research_result_degraded_and_collected() -> None:
    src = make_source("wikipedia")
    ok = FetchOutcome(source=SourceName.WIKIPEDIA, status=FetchStatus.OK, sources=[src])
    bad = FetchOutcome(source=SourceName.ARXIV, status=FetchStatus.TIMEOUT, error="t")

    no_answer = ResearchResult(question="q", outcomes=[ok, bad], answer=None)
    assert not no_answer.degraded  # no answer produced -> failed, not degraded
    assert no_answer.collected_sources == [src]

    answered = ResearchResult(
        question="q",
        outcomes=[ok, bad],
        answer=AnswerWithCitations(question="q", answer="a [1]"),
    )
    assert answered.degraded

    healthy = ResearchResult(
        question="q",
        outcomes=[ok],
        answer=AnswerWithCitations(question="q", answer="a [1]"),
    )
    assert not healthy.degraded
