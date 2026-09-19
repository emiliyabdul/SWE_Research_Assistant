"""Cache storage backends behind a single abstract interface.

Follows the same abstract-base-class + factory shape as the provided
``ai.providers`` package: callers depend on :class:`CacheStore` only and pick
a concrete backend via configuration (``CACHE_BACKEND``).

Stores hold opaque JSON strings with an absolute expiry timestamp; key
canonicalisation and (de)serialisation of domain objects belong to the cache
service layer, not here.
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from pathlib import Path

import aiosqlite

from researcher.config import CacheBackend, Settings

logger = logging.getLogger(__name__)


class CacheStore(ABC):
    """Async key-value store with per-entry TTL expiry."""

    @abstractmethod
    async def get(self, key: str) -> str | None:
        """Return the stored value for *key*, or ``None`` if absent or expired."""

    @abstractmethod
    async def set(self, key: str, value: str, ttl_seconds: float) -> None:
        """Store *value* under *key*, expiring ``ttl_seconds`` from now."""

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Remove *key* if present (no error when absent)."""

    @abstractmethod
    async def purge_expired(self) -> int:
        """Delete all expired entries; return how many were removed."""

    @abstractmethod
    async def close(self) -> None:
        """Release backend resources. The store must not be used afterwards."""


class MemoryCacheStore(CacheStore):
    """Process-local cache; useful for tests and ``CACHE_BACKEND=memory``."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[str, float]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> str | None:
        async with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if time.time() >= expires_at:
                del self._data[key]
                return None
            return value

    async def set(self, key: str, value: str, ttl_seconds: float) -> None:
        async with self._lock:
            self._data[key] = (value, time.time() + ttl_seconds)

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._data.pop(key, None)

    async def purge_expired(self) -> int:
        async with self._lock:
            now = time.time()
            expired = [k for k, (_, exp) in self._data.items() if now >= exp]
            for key in expired:
                del self._data[key]
            return len(expired)

    async def close(self) -> None:
        async with self._lock:
            self._data.clear()


class SqliteCacheStore(CacheStore):
    """Durable cache backed by a single SQLite file (via ``aiosqlite``).

    The connection is opened lazily on first use so constructing the store
    never touches the filesystem (important for fast CLI startup and tests).
    """

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS cache (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            expires_at REAL NOT NULL
        )
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._init_lock = asyncio.Lock()

    async def _connection(self) -> aiosqlite.Connection:
        async with self._init_lock:
            if self._conn is None:
                self._db_path.parent.mkdir(parents=True, exist_ok=True)
                self._conn = await aiosqlite.connect(self._db_path)
                await self._conn.execute("PRAGMA journal_mode=WAL")
                await self._conn.execute(self._SCHEMA)
                await self._conn.commit()
                logger.debug("sqlite cache opened at %s", self._db_path)
            return self._conn

    async def get(self, key: str) -> str | None:
        conn = await self._connection()
        async with conn.execute(
            "SELECT value, expires_at FROM cache WHERE key = ?", (key,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        value, expires_at = row
        if time.time() >= expires_at:
            await self.delete(key)
            return None
        return value

    async def set(self, key: str, value: str, ttl_seconds: float) -> None:
        conn = await self._connection()
        await conn.execute(
            "INSERT INTO cache (key, value, expires_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, expires_at = excluded.expires_at",
            (key, value, time.time() + ttl_seconds),
        )
        await conn.commit()

    async def delete(self, key: str) -> None:
        conn = await self._connection()
        await conn.execute("DELETE FROM cache WHERE key = ?", (key,))
        await conn.commit()

    async def purge_expired(self) -> int:
        conn = await self._connection()
        cursor = await conn.execute("DELETE FROM cache WHERE expires_at <= ?", (time.time(),))
        await conn.commit()
        return cursor.rowcount

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None


def create_cache_store(settings: Settings) -> CacheStore:
    """Factory: return the cache backend selected by configuration."""
    if settings.cache_backend is CacheBackend.MEMORY:
        return MemoryCacheStore()
    return SqliteCacheStore(settings.cache_db_path)
