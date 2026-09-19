"""Search-query relaxation for natural-language questions.

The source APIs behave very differently on full questions: Wikipedia's search
is title-based and returns nothing for
``"What is the current state of fusion energy research?"`` while finding
excellent results for ``"fusion energy"``. When a fetch returns zero results,
the orchestrator retries with progressively shorter keyword forms produced
here (bounded, and only on empty results — never extra load on the happy path).
"""

from __future__ import annotations

import re

_WORD = re.compile(r"[^\W_]+(?:-[^\W_]+)*", flags=re.UNICODE)

#: Question scaffolding and generic qualifiers that carry no search signal.
_STOPWORDS = frozenset(
    ["a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "at", "by", "with", "from", "as", "into", "between", "about", "what", "which", "who", "whom", "whose", "when", "where", "why", "how", "is", "are", "was", "were", "be", "been", "being", "do", "does", "did", "can", "could", "should", "would", "will", "shall", "may", "might", "must", "have", "has", "had", "it", "its", "this", "that", "these", "those", "there", "their", "his", "her", "our", "your", "my", "current", "state", "states", "latest", "recent", "new", "main", "stages", "stage", "overview", "status", "work", "works", "working", "handle", "handles", "handled"]
)


def content_words(question: str) -> list[str]:
    """Extract lower-cased content words, dropping question scaffolding."""
    return [w for w in (m.group(0).lower() for m in _WORD.finditer(question)) if w not in _STOPWORDS]


def relaxation_ladder(question: str) -> list[str]:
    """Progressively shorter keyword queries to try when a search comes up empty.

    Example: ``"What is the current state of fusion energy research?"`` →
    ``["fusion energy research", "fusion energy"]``.
    """
    words = content_words(question)
    if not words:
        return []
    ladder = [" ".join(words)]
    if len(words) > 3:
        ladder.append(" ".join(words[:3]))
    if len(words) > 2:
        ladder.append(" ".join(words[:2]))
    # drop duplicates and anything equal to the original question
    seen = {question.strip().lower()}
    unique: list[str] = []
    for candidate in ladder:
        if candidate not in seen:
            seen.add(candidate)
            unique.append(candidate)
    return unique
