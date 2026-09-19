# Architecture

One-page overview of the module boundaries, the data flow, and why each design
decision was made. The same diagram appears in the report and the slides.

## Diagram

```
                       +-----------------------------+
                       |      CLI (click)            |  python -m researcher ask
                       |  parse flags · validate     |  --sources --no-cache --json
                       |  render answer + refs       |  exit codes 0 / 1 / 2
                       +--------------+--------------+
                                      |  question, source subset
                                      v
                       +-----------------------------+
                       |   core / Researcher         |  sanitise + validate input
                       |   (business logic)          |  fetch -> synthesize -> result
                       +--------------+--------------+
                                      |
                                      v
                       +-----------------------------+
                       | concurrency / Orchestrator  |  asyncio.gather(return_exceptions=True)
                       |  asyncio.Semaphore(N)       |  per-source FetchOutcome, never raises
                       |  graceful degradation       |  sequential mode for the benchmark
                       +------+---------------+------+
                       cache  |               | fetch (on miss)
                              v               v
                +-------------------+  +-----------------------+
                | services/cache    |  | services/ai_service   |  tenacity retries (expo backoff)
                | SourceCache       |  | AIService             |  asyncio.timeout per attempt
                | canonical keys    |  | the ONLY module that  |  sync synthesize -> to_thread
                | TTL · eviction    |  | imports ai.* callables|  structured logging
                +---------+---------+  +-----------+-----------+
                          |                        |
                          v                        v
                +-------------------+  +-----------------------+
                | storage/          |  |  ai/  (PROVIDED,      |
                | CacheStore (ABC)  |  |  unmodified)          |
                |  - SqliteCacheStore |  fetch_wikipedia      |
                |  - MemoryCacheStore |  fetch_arxiv          |
                |                   |  |  fetch_web            |
                +-------------------+  |  synthesize (LLM)     |
                                       +-----------------------+
```

## Data flow for one question

1. **CLI** parses flags, builds the object graph (settings → store → cache →
   shared `httpx.AsyncClient` → AI service → orchestrator → researcher).
2. **Researcher** sanitises the question (control characters stripped; empty or
   oversized input rejected with a clean, user-facing error).
3. **Orchestrator** fans out to the requested sources with
   `asyncio.gather(return_exceptions=True)`, each task bounded by the
   semaphore. Per source: cache lookup → on miss, `AIService.fetch` → store.
4. **AIService** applies the resilience policy to every `ai.*` call: up to
   `RETRY_MAX_ATTEMPTS` attempts, exponential backoff between them, and an
   `asyncio.timeout` around each attempt. Only transient errors are retried.
5. Successful outcomes contribute their sources; failed ones carry an error
   message instead. If at least one source succeeded, **synthesize** produces
   the cited answer (a blocking SDK call, run in a worker thread).
6. The CLI renders the answer, numbered references, and a
   `Note: <source> unavailable ...` line for every degraded source.

## Design decisions and trade-offs

### The AI module is a hard boundary
Only `services/ai_service.py` imports `ai.*` callables. Business logic depends
on `AIService`, so the provided package stays a swappable external dependency
(and the smoke-test contract is easy to honour). The offline mode plugs in via
the *public* `llm=` parameter of `ai.synthesize` — no provided code is touched.

### asyncio over threads/processes
The workload is purely I/O-bound (three HTTP fetches + one LLM call). asyncio
gives the cheapest fan-out and native timeout/cancellation semantics.
Multiprocessing would buy nothing (no CPU-bound work) and complicate the cache.
The one blocking call (`ai.synthesize`) is isolated with `asyncio.to_thread`.

### Bounded parallelism
`asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)` caps in-flight fetches. With three
sources the default bound of 3 is effectively "all at once", but the knob
matters when batching many questions (e.g. the benchmark) and keeps us polite
against provider rate limits (arXiv asks for ~1 req/s).

### Two-layer failure isolation
`fetch_one` catches `AIServiceError` and returns a `FetchOutcome` — the
expected failure path. `gather(return_exceptions=True)` additionally isolates
*unexpected* exceptions so one broken task can never cancel its siblings.
Failures are **never cached**, so a flaky source recovers on the next run.

### Retry policy: transient-only
Retries (tenacity, exponential backoff) fire on `httpx` transport errors,
`ProviderError`, and attempt timeouts. `ValueError`/validation errors fail
immediately — retrying a malformed request only wastes rate-limit budget.

### Storage behind an ABC
`CacheStore` mirrors the ABC + factory shape of the provided `ai.providers`
package. SQLite (via `aiosqlite`, WAL mode) is the default: zero-setup,
container-friendly, durable across runs. The in-memory backend serves tests
and cache-bypass scenarios. A new backend is one class behind the same
interface, selected purely by `CACHE_BACKEND` — no caller changes.

### Cache keyed by canonicalised query
Keys are `source:canonical(query)` where canonicalisation casefolds, strips
punctuation and collapses whitespace — `"WHAT IS PHOTOSYNTHESIS?"` and
`"what is photosynthesis"` share an entry. TTL is configurable; `0` disables.
Corrupt entries are evicted and treated as misses, never as errors.

### Composition root in the CLI
All wiring happens in `cli.py`; every class takes its dependencies via the
constructor. That is what makes the test suite cheap: fakes slot in anywhere
without patching internals.

## Failure-mode walkthrough (for the report)

Scenario: arXiv times out while Wikipedia and web succeed.

```
WARNING ...ai_service: Retrying in 0.5 seconds as it raised TimeoutError
WARNING ...ai_service: Retrying in 1.0 seconds as it raised TimeoutError
ERROR   ...ai_service: fetch[arxiv]: giving up after 3 attempt(s) in 31.5s: TimeoutError()
WARNING ...orchestrator: source arxiv degraded (timeout): fetch[arxiv] failed after 3 attempt(s)
INFO    ...orchestrator: fetched 2/3 sources in 31.5s (parallel): wikipedia=ok, arxiv=timeout, web=ok
INFO    ...researcher: research complete in 33.1s: 2/3 sources, answer=yes (degraded)
```

The user still gets a cited answer plus
`Note: arxiv unavailable (timeout); answer based on remaining sources.`
