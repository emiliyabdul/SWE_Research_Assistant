"""Domain models owned by the SE layer.

These complement (never replace) the provided ``ai.schemas`` models: ``Source``,
``Citation`` and ``AnswerWithCitations`` cross the AI boundary unchanged, while
the models here describe *our* workflow around them — cache entries, per-source
fetch outcomes, and the overall research session result.
"""

from __future__ import annotations

import time
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from ai.schemas import AnswerWithCitations, Source


class SourceName(str, Enum):
    """The three research sources the orchestrator can query."""

    WIKIPEDIA = "wikipedia"
    ARXIV = "arxiv"
    WEB = "web"

    @classmethod
    def parse(cls, value: str) -> SourceName:
        """Parse a user-supplied source name, accepting common aliases."""
        normalized = value.strip().lower()
        aliases = {"wiki": cls.WIKIPEDIA, "wikipedia": cls.WIKIPEDIA, "arxiv": cls.ARXIV, "web": cls.WEB}
        try:
            return aliases[normalized]
        except KeyError:
            valid = ", ".join(sorted({a for a in aliases}))
            raise ValueError(f"unknown source {value!r}; valid: {valid}") from None


class FetchStatus(str, Enum):
    """How a single source fetch concluded."""

    OK = "ok"
    CACHED = "cached"
    TIMEOUT = "timeout"
    ERROR = "error"


class CacheEntry(BaseModel):
    """One cached fetch result, keyed by (source, canonical query)."""

    model_config = ConfigDict(frozen=True)

    source: SourceName
    query: str
    sources: list[Source]
    created_at: float = Field(default_factory=time.time)
    expires_at: float

    @property
    def is_expired(self) -> bool:
        return time.time() >= self.expires_at


class FetchOutcome(BaseModel):
    """Result of querying one source, successful or not.

    Failures carry an error message instead of raising, so one bad source
    never prevents the others from contributing (graceful degradation).
    """

    model_config = ConfigDict(frozen=True)

    source: SourceName
    status: FetchStatus
    sources: list[Source] = Field(default_factory=list)
    elapsed_seconds: float = 0.0
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status in (FetchStatus.OK, FetchStatus.CACHED)

    def to_dict(self) -> dict:
        """JSON-safe shape shared by the CLI's ``--json`` output and the web UI."""
        return {
            "source": self.source.value,
            "status": self.status.value,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "error": self.error,
            "excerpts": len(self.sources),
        }


class ResearchResult(BaseModel):
    """The complete outcome of one research question."""

    question: str
    outcomes: list[FetchOutcome]
    answer: AnswerWithCitations | None = None
    elapsed_seconds: float = 0.0

    @property
    def degraded(self) -> bool:
        """True when at least one source failed but an answer was still produced."""
        return self.answer is not None and any(not o.succeeded for o in self.outcomes)

    @property
    def collected_sources(self) -> list[Source]:
        """All sources gathered across successful outcomes, in stable order."""
        return [s for outcome in self.outcomes for s in outcome.sources]
