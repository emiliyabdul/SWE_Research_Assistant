"""Tests for query relaxation and the SE-layer web-search adapter selection."""

from __future__ import annotations

from researcher.config import Settings
from researcher.core.query import content_words, relaxation_ladder
from researcher.services.websearch import DdgsWebSearchProvider, create_web_search_provider


def test_content_words_drops_scaffolding() -> None:
    assert content_words("What is the current state of fusion energy research?") == [
        "fusion", "energy", "research",
    ]
    assert content_words("How does CRISPR-Cas9 gene editing work?") == [
        "crispr-cas9", "gene", "editing",
    ]


def test_relaxation_ladder_shortens_progressively() -> None:
    ladder = relaxation_ladder("What is the current state of fusion energy research?")
    assert ladder == ["fusion energy research", "fusion energy"]


def test_relaxation_ladder_short_question() -> None:
    assert relaxation_ladder("What is photosynthesis?") == ["photosynthesis"]


def test_relaxation_ladder_empty_for_scaffolding_only() -> None:
    assert relaxation_ladder("what is it?") == []


def test_relaxation_ladder_excludes_original() -> None:
    # already keyword-shaped: the full-keyword rung equals the original and is dropped
    ladder = relaxation_ladder("fusion energy")
    assert "fusion energy" not in [c for c in ladder]


def test_web_provider_selected_for_duckduckgo() -> None:
    provider = create_web_search_provider(Settings(web_search_provider="duckduckgo"))
    assert isinstance(provider, DdgsWebSearchProvider)


def test_web_provider_none_for_other_providers() -> None:
    assert create_web_search_provider(Settings(web_search_provider="tavily")) is None
    assert create_web_search_provider(Settings(web_search_provider="serper")) is None
