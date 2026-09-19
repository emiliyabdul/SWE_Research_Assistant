# Async Research Assistant

> Ask a research question; the system queries Wikipedia, arXiv and a web-search API **in parallel**, then synthesizes a single answer with inline `[N]` citations — wrapped in a production-grade SE layer: typed config, TTL cache, bounded concurrency, retries, validation, logging, tests, and Docker.

**Team:** Azeri40 · emiliyabdul · thuseynowa  •  **Topic:** 4 — Async Research Assistant  •  **Course:** AI-ENG-110 Software Engineering, AI Academy

---

## Quick start

```bash
# 1. Clone & install
git clone https://github.com/Azeri40/swe_final_new
cd swe_final_new
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -e .                   # installs `researcher` and the provided `ai` package

# 2. Configure
cp .env.example .env               # then fill in real API keys
# (DO NOT commit .env — it is in .gitignore)

# 3. Run the tests
pytest tests/test_ai_smoke.py -v   # provided smoke tests (16)
pytest --cov                       # full suite (125 tests, all offline)

# 4. Run the demo — no API keys needed
python -m researcher ask --offline "What is photosynthesis and what are its main stages?"
```

## CLI usage

```bash
python -m researcher ask "your question"              # live mode (keys from .env)
python -m researcher ask --offline "your question"    # keyless, network-free demo
python -m researcher ask --sources wiki,arxiv "q"     # restrict to a source subset
python -m researcher ask --no-cache "q"               # bypass the TTL cache
python -m researcher ask --json "q"                   # machine-readable output
python -m researcher ask --sequential "q"             # one-at-a-time fetching (benchmark aid)
```

Exit codes: `0` answer produced (possibly degraded) · `1` no answer could be produced · `2` invalid input.

If a source fails, the answer is still produced from the remaining ones, with a note:

```
Note: arxiv unavailable (timeout); answer based on remaining sources.
```

## Web UI (optional)

A FastAPI backend + static HTML/CSS/JS frontend over the same composition
root the CLI uses — nothing in the core depends on it:

```bash
pip install -r requirements-ui.txt
python -m uvicorn ui.server:app --reload
```

Then open http://127.0.0.1:8000/. Toggles for offline/live mode, cache,
sequential fetching and source subset; shows the answer with linked
references, per-source status badges and timings, and a session history in
the sidebar. Offline mode works with zero setup.

The same backend also exposes the plain JSON API it's built on, if you just
want to call it directly:

```bash
curl -s localhost:8000/api/ask -H 'content-type: application/json' \
    -d '{"question": "What is photosynthesis?", "offline": true}'
curl -s localhost:8000/api/healthz   # confirms the cache backend is reachable
```

## Run with Docker

```bash
docker build -t researcher .
docker run --rm researcher                                          # offline demo (no keys)
docker run --rm --env-file .env researcher \
    python -m researcher ask "your question"                        # live mode
docker run --rm researcher python -m pytest tests -q                # full suite in-container
```

Two-stage build (builder installs deps into a venv; runtime copies the venv + source) on `python:3.14-alpine`.

## Environment variables

| Variable | Required? | Default | What it controls |
|---|---|---|---|
| `LLM_PROVIDER` | live mode | `anthropic` | `anthropic` \| `openai` \| `gemini` |
| `LLM_MODEL` | live mode | `claude-sonnet-4-6` | model id |
| `LLM_MAX_TOKENS` | no | `4096` | completion budget (reasoning models think inside it) |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GOOGLE_API_KEY` | one of, live mode | — | key for the chosen provider |
| `WEB_SEARCH_PROVIDER` | no | `duckduckgo` | `tavily` \| `serper` \| `duckduckgo` (ddg needs no key) |
| `LOG_LEVEL` | no | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `CACHE_BACKEND` | no | `sqlite` | `sqlite` \| `memory` |
| `CACHE_DIR` | no | `./.cache` | location of the SQLite cache file |
| `CACHE_TTL_SECONDS` | no | `86400` | cache entry lifetime; `0` disables caching |
| `PER_SOURCE_TIMEOUT_SECONDS` | no | `10` | per-attempt timeout for each source fetch |
| `LLM_TIMEOUT_SECONDS` | no | `30` | per-attempt timeout for synthesis |
| `MAX_CONCURRENT_REQUESTS` | no | `3` | semaphore bound for parallel fetches |
| `MAX_SOURCES_PER_QUERY` | no | `3` | excerpts requested per source |
| `RETRY_MAX_ATTEMPTS` | no | `3` | attempts per external call |
| `RETRY_BACKOFF_BASE_SECONDS` | no | `0.5` | exponential backoff base |
| `RETRY_BACKOFF_MAX_SECONDS` | no | `8` | backoff ceiling |
| `MAX_QUESTION_LENGTH` | no | `500` | input validation bound |

The full list is in `.env.example`. **Do not commit a real `.env`.**

## Bonus features

| Bonus | How to see it |
|---|---|
| **GitHub Actions CI** | `.github/workflows/ci.yml`: ruff + mypy, pytest with a ≥60% coverage gate, and a Docker build that runs the demo and the in-container suite. |
| **Multi-stage Dockerfile** | builder (installs deps into a venv) → `python:3.14-alpine` runtime (copies the venv + source). |

## Sequential vs concurrent benchmark

| Workload | N | Sequential | Concurrent (sem=3) | Speedup |
|---|---|---|---|---|
| 5 sample questions × 3 sources, offline, cache disabled | 15 fetches | 2.42 s | 0.83 s | **2.92×** |

**Reproduce:**
```bash
python scripts/bench.py --offline --runs 3
```

The theoretical maximum for three equally-slow sources is 3×. After parallelizing the fetches, the new bottleneck is the **synthesis step**, which is a single LLM call and runs serially in both modes (plus, in live mode, per-provider rate limits enforced by the semaphore). See `report/report.pdf` for details.

## Testing

```bash
pytest --cov
```

- Total coverage: **95%** on `src/researcher` (course gate: 60%)
- 92 SE-layer tests + 16 provided AI smoke tests = **108 collected**, all pass on any machine (no service dependencies, nothing skipped)
- All tests run offline: `ai.*` is monkeypatched at the module boundary, end-to-end paths use the offline service, no test touches the network
- Concurrency-specific tests: one-source failure degrades gracefully, unexpected exceptions are isolated, semaphore peak equals its bound, failures are never cached

## Project layout

```
.
├── ai/                        # PROVIDED — do not modify
├── src/researcher/
│   ├── config.py              # typed settings from .env (pydantic-settings)
│   ├── models.py              # SE-layer domain models
│   ├── services/
│   │   ├── ai_service.py      # retries + timeouts + logging around every ai.* call
│   │   ├── cache.py           # TTL cache with canonical (source, query) keys
│   │   └── offline.py         # keyless demo service (fake LLM via public llm= param)
│   ├── core/researcher.py     # business logic: validate -> fetch -> synthesize
│   ├── concurrency/orchestrator.py  # asyncio.gather + semaphore + degradation
│   ├── storage/cache_store.py # CacheStore ABC: SQLite + in-memory backends
│   ├── cli.py                 # click CLI + composition root
│   └── __main__.py            # python -m researcher
├── tests/
│   ├── test_ai_smoke.py       # PROVIDED contract tests
│   └── se/                    # our 92 offline tests
├── data/                      # sample research questions
├── artefacts/                 # outputs of a full demo run
├── scripts/bench.py           # sequential-vs-parallel benchmark
├── docs/architecture.md       # architecture diagram + design decisions
├── Dockerfile
├── requirements.txt           # every dependency pinned
├── .env.example
└── README.md
```

## Architecture in one diagram

```
            +-----------------------------+
            |      CLI (click)            |   python -m researcher ask
            |  validation · exit codes    |
            +--------------+--------------+
                           v
            +-----------------------------+
            |   core / Researcher         |   validate -> fetch -> synthesize
            +--------------+--------------+
                           v
            +-----------------------------+
            | concurrency / Orchestrator  |   asyncio.gather(return_exceptions=True)
            |  semaphore · degradation    |   per-source outcomes, never raises
            +------+---------------+------+
                   v               v
     +-------------------+  +-----------------------+
     | services/cache    |  | services/ai_service   |   retries · timeouts · logging
     |  TTL · canonical  |  |  (only module that    |
     |  keys             |  |   touches ai.*)       |
     +---------+---------+  +-----------+-----------+
               v                        v
     +-------------------+  +-----------------------+
     | storage/          |  |  ai/  (PROVIDED)      |
     |  CacheStore ABC   |  |  fetchers · synthesize|
     |  sqlite | memory  |  +-----------------------+
     +-------------------+
```

Full rationale (module boundaries, concurrency model, storage choice, trade-offs) in [docs/architecture.md](docs/architecture.md).

## Limitations

- The cache stores fetch results only; synthesized answers are not cached, so repeating a question re-pays the LLM cost (deliberate: answers should reflect the latest sources within the TTL).
- Rate limiting is a fixed semaphore + exponential backoff on 429-class errors; there is no adaptive token-bucket tuned per provider.

## Git workflow

`main` is the reviewed branch; features land via PRs from short-lived branches (see `.github/pull_request_template.md`). The `prototype` branch holds the reference implementation being ported module-by-module via reviewed PRs.

## Tools & acknowledgements

AI assistants (GitHub Copilot) were used during development, as disclosed in the report and the contribution statement. All commits carry a `Co-authored-by: Copilot` trailer where applicable.

## License

Academic coursework for AI-ENG-110; not a published library.
