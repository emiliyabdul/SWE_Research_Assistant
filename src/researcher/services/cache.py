"""TTL-aware cache for source fetch results.

Sits between the orchestrator and a :class:`~researcher.storage.cache_store.CacheStore`
backend. Owns the two concerns the stores deliberately do not:

* **Key canonicalisation** — ``"WHAT IS PHOTOSYNTHESIS?"`` and
  ``"what is photosynthesis"`` must hit the same cache entry.
* **(De)serialisation** — ``list[Source]`` round-trips through JSON, and a
  corrupt or incompatible stored payload is treated as a miss (and evicted),
  never as an error.
* **Mode isolation** — offline runs store canned sources; a ``namespace``
  prefix keeps them from ever being served to a live run (and vice versa).

A TTL of ``0`` disables caching entirely; the CLI ``--no-cache`` flag maps to
``enabled=False``.
"""

from __future__ import annotations

import json
import logging
import re

from pydantic import TypeAdapter, ValidationError

from ai.schemas import Source
from researcher.models import SourceName
from researcher.storage.cache_store import CacheStore

logger = logging.getLogger(__name__)

_SOURCES_ADAPTER: TypeAdapter[list[Source]] = TypeAdapter(list[Source])
_PUNCTUATION = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WHITESPACE = re.compile(r"\s+")


def canonicalize_query(query: str) -> str:
    """Normalise a query for cache keying: casefold, strip punctuation, collapse spaces."""
    lowered = query.casefold()
    no_punct = _PUNCTUATION.sub(" ", lowered)
    return _WHITESPACE.sub(" ", no_punct).strip()


class SourceCache:
    """Caches ``list[Source]`` per ``(source, canonical query)`` with a TTL."""

    def __init__(
        self,
        store: CacheStore,
        *,
        ttl_seconds: float,
        enabled: bool = True,
        namespace: str = "",
    ) -> None:
        self._store = store
        self._ttl_seconds = ttl_seconds
        self._enabled = enabled and ttl_seconds > 0
        self._namespace = namespace

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _key(self, source: SourceName, query: str) -> str:
        prefix = f"{self._namespace}:" if self._namespace else ""
        return f"{prefix}{source.value}:{canonicalize_query(query)}"

    async def get(self, source: SourceName, query: str) -> list[Source] | None:
        """Return cached sources, or ``None`` on miss/expiry/corruption/disabled."""
        if not self._enabled:
            return None
        key = self._key(source, query)
        raw = await self._store.get(key)
        if raw is None:
            logger.debug("cache miss: %s", key)
            return None
        try:
            sources = _SOURCES_ADAPTER.validate_json(raw)
        except (ValidationError, json.JSONDecodeError) as exc:
            logger.warning("cache entry corrupt, evicting %s: %s", key, exc)
            await self._store.delete(key)
            return None
        logger.info("cache hit: %s (%d sources)", key, len(sources))
        return sources

    async def set(self, source: SourceName, query: str, sources: list[Source]) -> None:
        """Store fetch results under the canonical key (no-op when disabled)."""
        if not self._enabled:
            return
        key = self._key(source, query)
        payload = _SOURCES_ADAPTER.dump_json(sources).decode("utf-8")
        await self._store.set(key, payload, self._ttl_seconds)
        logger.debug("cache store: %s (%d sources, ttl=%.0fs)", key, len(sources), self._ttl_seconds)

    async def purge_expired(self) -> int:
        """Evict expired entries from the backend; returns the count removed."""
        return await self._store.purge_expired()

    async def close(self) -> None:
        await self._store.close()
