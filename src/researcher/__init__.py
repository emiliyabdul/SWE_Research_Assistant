"""Async Research Assistant — software-engineering layer.

Wraps the provided ``ai/`` package (source fetchers + LLM synthesizer) with
configuration, caching, concurrency orchestration, retries, logging, and a CLI.
The ``ai/`` package itself is a course-provided contract and is never modified.
"""

__version__ = "0.1.0"
