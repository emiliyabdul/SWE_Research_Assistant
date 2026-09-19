"""Tests for the TTL cache service: canonical keys, serialisation, bypass."""

from __future__ import annotations

import asyncio

import pytest

from researcher.models import SourceName
from researcher.services.cache import SourceCache, canonicalize_query
from researcher.storage.cache_store import MemoryCacheStore
from tests.se.conftest import make_source


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("WHAT IS PHOTOSYNTHESIS?", "what is photosynthesis"),
        ("  what,   is\tphotosynthesis!! ", "what is photosynthesis"),
        ("State-of-the-Art NLP", "state of the art nlp"),
    ],
)
def test_canonicalize_query(raw: str, expected: str) -> None:
    assert canonicalize_query(raw) == expected


@pytest.mark.asyncio
async def test_roundtrip_and_equivalent_phrasings(mem_cache: SourceCache) -> None:
    sources = [make_source("wikipedia")]
    assert await mem_cache.get(SourceName.WIKIPEDIA, "what is photosynthesis") is None
    await mem_cache.set(SourceName.WIKIPEDIA, "what is photosynthesis", sources)
    assert await mem_cache.get(SourceName.WIKIPEDIA, "WHAT IS PHOTOSYNTHESIS?") == sources


@pytest.mark.asyncio
async def test_keys_isolated_per_source(mem_cache: SourceCache) -> None:
    await mem_cache.set(SourceName.WIKIPEDIA, "q", [make_source("wikipedia")])
    assert await mem_cache.get(SourceName.ARXIV, "q") is None


@pytest.mark.asyncio
async def test_namespace_isolates_offline_from_live() -> None:
    store = MemoryCacheStore()
    offline_cache = SourceCache(store, ttl_seconds=60, namespace="offline")
    live_cache = SourceCache(store, ttl_seconds=60)

    await offline_cache.set(SourceName.WIKIPEDIA, "q", [make_source("wikipedia")])
    assert await live_cache.get(SourceName.WIKIPEDIA, "q") is None  # canned data never leaks
    assert await offline_cache.get(SourceName.WIKIPEDIA, "q") is not None


@pytest.mark.asyncio
async def test_corrupt_entries_evicted() -> None:
    store = MemoryCacheStore()
    cache = SourceCache(store, ttl_seconds=60)

    await store.set("wikipedia:bad", "{not json", 60)
    assert await cache.get(SourceName.WIKIPEDIA, "bad") is None
    assert await store.get("wikipedia:bad") is None

    await store.set("wikipedia:bad2", '[{"wrong": "shape"}]', 60)
    assert await cache.get(SourceName.WIKIPEDIA, "bad2") is None


@pytest.mark.asyncio
async def test_disabled_cache_bypasses() -> None:
    cache = SourceCache(MemoryCacheStore(), ttl_seconds=60, enabled=False)
    await cache.set(SourceName.WEB, "q", [make_source("web")])
    assert await cache.get(SourceName.WEB, "q") is None
    assert not cache.enabled


@pytest.mark.asyncio
async def test_zero_ttl_disables() -> None:
    cache = SourceCache(MemoryCacheStore(), ttl_seconds=0)
    await cache.set(SourceName.WEB, "q", [make_source("web")])
    assert await cache.get(SourceName.WEB, "q") is None
    assert not cache.enabled


@pytest.mark.asyncio
async def test_ttl_expiry_through_service() -> None:
    cache = SourceCache(MemoryCacheStore(), ttl_seconds=0.01)
    await cache.set(SourceName.WEB, "q", [make_source("web")])
    await asyncio.sleep(0.05)
    assert await cache.get(SourceName.WEB, "q") is None
