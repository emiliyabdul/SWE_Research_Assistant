"""Typed application configuration loaded from the environment / ``.env``.

Every tunable of the SE layer lives here so behaviour is reproducible and
documented in one place. The provided ``ai/`` package reads its own env vars
(``LLM_PROVIDER``, ``WEB_SEARCH_PROVIDER``, API keys) directly; those are
mirrored here read-only so they can be logged and validated at startup
without ever touching the ``ai/`` internals.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class CacheBackend(str, Enum):
    """Available cache storage backends."""

    SQLITE = "sqlite"
    MEMORY = "memory"


class LogLevel(str, Enum):
    """Accepted logging levels (mirrors the stdlib ``logging`` names)."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class Settings(BaseSettings):
    """All SE-layer settings, populated from environment variables / ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    # --- AI module selection (consumed by ai/; mirrored here for visibility) ---
    llm_provider: str = Field(default="anthropic", description="anthropic | openai | gemini")
    llm_model: str = Field(default="claude-sonnet-4-6")
    web_search_provider: str = Field(default="duckduckgo", description="tavily | serper | duckduckgo")

    # --- logging ---
    log_level: LogLevel = LogLevel.INFO

    # --- caching ---
    cache_backend: CacheBackend = CacheBackend.SQLITE
    cache_dir: Path = Path("./.cache")
    cache_ttl_seconds: int = Field(default=86_400, ge=0)

    # --- concurrency / timeouts ---
    per_source_timeout_seconds: float = Field(default=10.0, gt=0)
    llm_timeout_seconds: float = Field(default=30.0, gt=0)
    llm_max_tokens: int = Field(default=4096, ge=256, description="Completion budget; reasoning models spend part of it on thinking")
    max_concurrent_requests: int = Field(default=3, ge=1, description="Semaphore bound for parallel fetches")
    max_sources_per_query: int = Field(default=3, ge=1)

    # --- retry policy ---
    retry_max_attempts: int = Field(default=3, ge=1)
    retry_backoff_base_seconds: float = Field(default=0.5, gt=0)
    retry_backoff_max_seconds: float = Field(default=8.0, gt=0)

    # --- input validation bounds ---
    max_question_length: int = Field(default=500, ge=1)

    @field_validator("llm_provider", "web_search_provider", mode="before")
    @classmethod
    def _lowercase(cls, value: str) -> str:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("log_level", "cache_backend", mode="before")
    @classmethod
    def _normalize_enum_case(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            return value.upper() if value.upper() in LogLevel.__members__ else value.lower()
        return value

    @property
    def cache_db_path(self) -> Path:
        """Location of the SQLite cache database file."""
        return self.cache_dir / "cache.db"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton (loaded once, cached)."""
    return Settings()
