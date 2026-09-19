"""Tests for the cache storage backends (memory + sqlite) and factory."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from researcher.config import Settings
from researcher.storage.cache_store import (
    CacheStore,
    MemoryCacheStore,
    SqliteCacheStore,
    create_cache_store,
)


@pytest.fixture(params=["memory", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> CacheStore:
    if request.param == "memory":
        return MemoryCacheStore()
    return SqliteCacheStore(tmp_path / "cache.db")


@pytest.mark.asyncio
async def test_get_missing_returns_none(store: CacheStore) -> None:
    assert await store.get("nope") is None
    await store.close()


@pytest.mark.asyncio
async def test_set_get_roundtrip(store: CacheStore) -> None:
    await store.set("k", "v", ttl_seconds=60)
    assert await store.get("k") == "v"
    await store.close()


@pytest.mark.asyncio
async def test_set_overwrites(store: CacheStore) -> None:
    await store.set("k", "v1", ttl_seconds=60)
    await store.set("k", "v2", ttl_seconds=60)
    assert await store.get("k") == "v2"
    await store.close()


@pytest.mark.asyncio
async def test_expiry(store: CacheStore) -> None:
    await store.set("k", "v", ttl_seconds=0.01)
    await asyncio.sleep(0.05)
    assert await store.get("k") is None
    await store.close()


@pytest.mark.asyncio
async def test_delete_is_idempotent(store: CacheStore) -> None:
    await store.set("k", "v", ttl_seconds=60)
    await store.delete("k")
    await store.delete("k")
    assert await store.get("k") is None
    await store.close()


@pytest.mark.asyncio
async def test_purge_expired(store: CacheStore) -> None:
    await store.set("live", "v", ttl_seconds=60)
    await store.set("dead", "v", ttl_seconds=0.01)
    await asyncio.sleep(0.05)
    purged = await store.purge_expired()
    assert purged == 1
    assert await store.get("live") == "v"
    await store.close()


@pytest.mark.asyncio
async def test_sqlite_persists_across_connections(tmp_path: Path) -> None:
    db = tmp_path / "cache.db"
    first = SqliteCacheStore(db)
    await first.set("k", "v", ttl_seconds=60)
    await first.close()

    second = SqliteCacheStore(db)
    assert await second.get("k") == "v"
    await second.close()


def test_factory_selects_backend() -> None:
    assert isinstance(create_cache_store(Settings(cache_backend="memory")), MemoryCacheStore)
    assert isinstance(create_cache_store(Settings(cache_backend="sqlite")), SqliteCacheStore)
