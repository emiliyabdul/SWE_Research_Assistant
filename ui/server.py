"""FastAPI backend for the web frontend.

A thin HTTP layer over the same composition root the CLI uses
(:func:`researcher.cli.run_ask`) — no business logic lives here. The static
frontend under ``ui/static`` talks to this over JSON; nothing about the
researcher package changes to support it.

Run from the repository root (after ``pip install -r requirements-ui.txt``
and ``pip install -e .``):

    python -m uvicorn ui.server:app --reload

Then open http://127.0.0.1:8000/
"""

from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# The provided ai/ package reads provider settings (LLM_PROVIDER, *_API_KEY)
# straight from os.environ, so .env must be exported here — mirroring the CLI.
load_dotenv()

from researcher.cli import parse_sources, run_ask
from researcher.config import get_settings
from researcher.core.researcher import InvalidQuestionError
from researcher.models import ResearchResult
from researcher.services.openrouter import OPENROUTER_MODELS, openrouter_available
from researcher.storage.cache_store import create_cache_store

STATIC_DIR = Path(__file__).parent / "static"
SAMPLE_QUESTIONS_PATH = Path(__file__).resolve().parent.parent / "data" / "research_questions.json"

app = FastAPI(title="Async Research Assistant API")

# The static page is served from the same origin in normal use; CORS is
# opened up anyway so the frontend can also be pointed at a backend running
# on a different port during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AskRequest(BaseModel):
    question: str
    sources: str | None = None  # comma-separated: "wiki,arxiv,web"; None = all
    use_cache: bool = True
    sequential: bool = False
    offline: bool = True
    llm_model: str | None = None  # OpenRouter model id; None = configured provider


def result_to_dict(result: ResearchResult) -> dict:
    """UI-facing shape: same underlying data as ``python -m researcher ask
    --json`` (see :func:`researcher.cli.result_to_dict`), keyed as
    ``outcomes`` for the frontend's table view. Per-outcome fields come from
    :meth:`FetchOutcome.to_dict`, shared with the CLI's JSON output."""
    return {
        "question": result.question,
        "answer": result.answer.to_dict() if result.answer else None,
        "degraded": result.degraded,
        "elapsed_seconds": round(result.elapsed_seconds, 3),
        "outcomes": [o.to_dict() for o in result.outcomes],
    }


@app.get("/api/settings")
def read_settings() -> dict:
    """Active configuration, for display in the UI (no secrets included)."""
    s = get_settings()
    return {
        "llm_provider": s.llm_provider,
        "llm_model": s.llm_model,
        "web_search_provider": s.web_search_provider,
        "cache_backend": s.cache_backend.value,
        "cache_ttl_seconds": s.cache_ttl_seconds,
        "max_concurrent_requests": s.max_concurrent_requests,
        "retry_max_attempts": s.retry_max_attempts,
    }


@app.get("/api/models")
def read_models() -> dict:
    """LLM choices for the UI dropdown: the configured default plus any
    OpenRouter models (marked unavailable when no key is set)."""
    s = get_settings()
    available = openrouter_available()
    return {
        "models": [
            {
                "id": None,
                "label": f"Default — {s.llm_provider} / {s.llm_model}",
                "available": True,
            },
            *[
                {"id": model_id, "label": label, "available": available}
                for model_id, label in OPENROUTER_MODELS.items()
            ],
        ]
    }


@app.get("/api/sample-questions")
def sample_questions() -> dict:
    """The project's real sample question set, for empty-state suggestions."""
    if not SAMPLE_QUESTIONS_PATH.exists():
        return {"questions": []}
    return json.loads(SAMPLE_QUESTIONS_PATH.read_text(encoding="utf-8"))


@app.get("/api/healthz")
async def healthz() -> dict:
    """Liveness check: confirms the configured cache backend is reachable."""
    settings = get_settings()
    store = create_cache_store(settings)
    try:
        await store.get("healthz")
    except Exception as exc:  # backend-specific errors surface as a 503, not a crash
        raise HTTPException(status_code=503, detail=f"cache backend unreachable: {exc}") from exc
    finally:
        await store.close()
    return {"status": "ok", "cache_backend": settings.cache_backend.value}


@app.post("/api/ask")
async def ask(req: AskRequest) -> dict:
    settings = get_settings()

    try:
        sources = parse_sources(req.sources)
    except (InvalidQuestionError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if req.llm_model is not None and req.llm_model not in OPENROUTER_MODELS:
        raise HTTPException(status_code=422, detail=f"Unknown model: {req.llm_model}")

    try:
        result = await run_ask(
            settings,
            req.question,
            sources,
            use_cache=req.use_cache,
            sequential=req.sequential,
            offline=req.offline,
            # Offline mode uses the canned LLM; the model choice applies to live runs.
            llm_model=None if req.offline else req.llm_model,
        )
    except InvalidQuestionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return result_to_dict(result)


# Declared last: everything not matched by /api/* above falls through to the
# static file server (and "/" serves static/index.html).
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
