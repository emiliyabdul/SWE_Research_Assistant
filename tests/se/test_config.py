"""Tests for the typed configuration layer."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from researcher.config import CacheBackend, LogLevel, Settings, get_settings


def test_defaults_are_sensible(monkeypatch: pytest.MonkeyPatch) -> None:
    # Isolate from ambient environment (e.g. docker-compose sets CACHE_BACKEND).
    for var in ("CACHE_BACKEND", "LOG_LEVEL", "CACHE_TTL_SECONDS", "MAX_CONCURRENT_REQUESTS"):
        monkeypatch.delenv(var, raising=False)
    s = Settings(_env_file=None)
    assert s.cache_backend is CacheBackend.SQLITE
    assert s.log_level is LogLevel.INFO
    assert s.cache_ttl_seconds == 86_400
    assert s.max_concurrent_requests >= 1
    assert s.retry_max_attempts >= 1


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CACHE_TTL_SECONDS", "120")
    monkeypatch.setenv("MAX_CONCURRENT_REQUESTS", "7")
    s = Settings()
    assert s.cache_ttl_seconds == 120
    assert s.max_concurrent_requests == 7


def test_enum_parsing_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("CACHE_BACKEND", "MEMORY")
    s = Settings()
    assert s.log_level is LogLevel.DEBUG
    assert s.cache_backend is CacheBackend.MEMORY


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("log_level", "NOPE"),
        ("cache_backend", "redis"),
        ("cache_ttl_seconds", -1),
        ("max_concurrent_requests", 0),
        ("retry_max_attempts", 0),
        ("per_source_timeout_seconds", 0),
    ],
)
def test_invalid_values_rejected(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        # Deliberately dynamic: exercises invalid input across heterogeneous
        # field types, which a static signature can't express.
        Settings(**{field: value})  # type: ignore[arg-type]


def test_provider_names_normalised() -> None:
    s = Settings(llm_provider="  Anthropic ", web_search_provider="DUCKDUCKGO")
    assert s.llm_provider == "anthropic"
    assert s.web_search_provider == "duckduckgo"


def test_cache_db_path_derived_from_cache_dir() -> None:
    s = Settings(cache_dir=Path("/tmp/x"))
    assert s.cache_db_path == Path("/tmp/x") / "cache.db"


def test_settings_frozen() -> None:
    s = Settings()
    with pytest.raises(ValidationError):
        s.cache_ttl_seconds = 1  # type: ignore[misc]


def test_get_settings_is_cached() -> None:
    get_settings.cache_clear()
    assert get_settings() is get_settings()
    get_settings.cache_clear()
