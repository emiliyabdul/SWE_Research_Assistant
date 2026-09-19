"""Tests for the CLI: flags, rendering, exit codes (all offline)."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from ai.schemas import AnswerWithCitations, Citation
from researcher.cli import cli, parse_sources, render_result
from researcher.concurrency.orchestrator import DEFAULT_SOURCES
from researcher.config import get_settings
from researcher.core.researcher import InvalidQuestionError
from researcher.models import FetchOutcome, FetchStatus, ResearchResult, SourceName
from tests.se.conftest import make_source


@pytest.fixture(autouse=True)
def clean_settings(monkeypatch: pytest.MonkeyPatch):
    """Isolate every CLI test: memory cache backend, fresh settings singleton."""
    monkeypatch.setenv("CACHE_BACKEND", "memory")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# --- parse_sources ------------------------------------------------------------

def test_parse_sources_default_is_all() -> None:
    assert parse_sources(None) == DEFAULT_SOURCES


def test_parse_sources_subset_with_aliases_and_dedup() -> None:
    assert parse_sources("wiki, arxiv, wikipedia") == (SourceName.WIKIPEDIA, SourceName.ARXIV)


def test_parse_sources_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="unknown source"):
        parse_sources("wiki,bing")


def test_parse_sources_rejects_empty_spec() -> None:
    with pytest.raises(InvalidQuestionError):
        parse_sources(" , ,")


# --- render_result -------------------------------------------------------------

def test_render_includes_answer_references_and_notes() -> None:
    src = make_source("wikipedia", "Photosynthesis")
    result = ResearchResult(
        question="q?",
        outcomes=[
            FetchOutcome(source=SourceName.WIKIPEDIA, status=FetchStatus.OK, sources=[src]),
            FetchOutcome(source=SourceName.ARXIV, status=FetchStatus.TIMEOUT, error="t"),
        ],
        answer=AnswerWithCitations(
            question="q?", answer="Answer [1].",
            citations=[Citation(index=1, source=src)],
        ),
        elapsed_seconds=0.5,
    )
    text = render_result(result)
    assert "A: Answer [1]." in text
    assert "[1] (wikipedia) Photosynthesis" in text
    assert "Note: arxiv unavailable (timeout)" in text
    assert "1/2 sources" in text


def test_render_without_answer() -> None:
    result = ResearchResult(
        question="q?",
        outcomes=[FetchOutcome(source=SourceName.WEB, status=FetchStatus.ERROR, error="x")],
        answer=None,
    )
    assert "No answer could be produced" in render_result(result)


# --- ask command ----------------------------------------------------------------

def test_ask_offline_happy_path(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["ask", "--offline", "--no-cache", "What is photosynthesis?"])
    assert result.exit_code == 0, result.output
    assert "A:" in result.output
    assert "References:" in result.output
    assert "(3/3 sources" in result.output


def test_ask_json_output(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["ask", "--offline", "--no-cache", "--json", "json test"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["question"] == "json test"
    assert payload["answer"]["citations"]
    assert {s["source"] for s in payload["sources"]} == {"wikipedia", "arxiv", "web"}
    assert payload["degraded"] is False


def test_ask_sources_subset(runner: CliRunner) -> None:
    result = runner.invoke(
        cli, ["ask", "--offline", "--no-cache", "--sources", "wiki,arxiv", "subset test"]
    )
    assert result.exit_code == 0, result.output
    assert "(2/2 sources" in result.output
    assert "(web)" not in result.output


def test_ask_unknown_source_is_usage_error(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["ask", "--offline", "--sources", "bing", "q"])
    assert result.exit_code == 2
    assert "unknown source" in result.output


def test_ask_empty_question_is_usage_error(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["ask", "--offline", "   "])
    assert result.exit_code == 2
    assert "must not be empty" in result.output


def test_ask_oversized_question_is_usage_error(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["ask", "--offline", "x" * 501])
    assert result.exit_code == 2
    assert "too long" in result.output


def test_ask_cache_roundtrip_within_process(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    # sqlite backend in a temp dir so two invocations share the cache
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CACHE_BACKEND", "sqlite")
    monkeypatch.setenv("CACHE_DIR", "./.cache")
    get_settings.cache_clear()

    first = runner.invoke(cli, ["ask", "--offline", "cache me"])
    assert first.exit_code == 0, first.output
    assert "cached" not in first.output

    second = runner.invoke(cli, ["ask", "--offline", "CACHE ME!"])
    assert second.exit_code == 0, second.output
    assert "cached" in second.output
