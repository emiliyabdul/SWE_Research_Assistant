"""Benchmark: sequential vs concurrent source fetching over the sample questions.

Runs every question from ``data/research_questions.json`` twice — once with the
three sources fetched one at a time, once with them fetched in parallel — under
identical conditions (cache disabled for both, same AI service, same timeouts),
then prints a per-question and aggregate comparison table.

Usage (from the repository root):

    python scripts/bench.py --offline              # no keys, no network
    python scripts/bench.py                        # live providers from .env
    python scripts/bench.py --offline --runs 3     # average over 3 runs

The table (and the exact command) belong in the README and the report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from researcher.cli import make_http_client, setup_logging
from researcher.concurrency.orchestrator import ResearchOrchestrator
from researcher.config import get_settings
from researcher.core.researcher import Researcher
from researcher.services.ai_service import AIService
from researcher.services.cache import SourceCache
from researcher.services.offline import OfflineAIService
from researcher.storage.cache_store import MemoryCacheStore


def load_questions() -> list[str]:
    payload = json.loads((REPO_ROOT / "data" / "research_questions.json").read_text(encoding="utf-8"))
    return [q["text"] for q in payload["questions"]]


async def time_run(researcher: Researcher, question: str, *, sequential: bool) -> tuple[float, int]:
    """Return (wall-time seconds, sources succeeded) for one question."""
    started = time.perf_counter()
    result = await researcher.run(question, sequential=sequential)
    elapsed = time.perf_counter() - started
    return elapsed, sum(1 for o in result.outcomes if o.succeeded)


async def bench(offline: bool, runs: int) -> None:
    settings = get_settings()
    questions = load_questions()

    rows: list[tuple[str, float, float, int]] = []
    async with make_http_client(settings.per_source_timeout_seconds) as client:
        ai_service = OfflineAIService(settings) if offline else AIService(settings, client=client)

        def make_researcher() -> Researcher:
            # Fresh disabled cache per run: both modes always hit the sources.
            cache = SourceCache(MemoryCacheStore(), ttl_seconds=0)
            return Researcher(settings, ResearchOrchestrator(settings, ai_service, cache), ai_service)

        for question in questions:
            seq_times, par_times, ok_counts = [], [], []
            for _ in range(runs):
                seq, _ok = await time_run(make_researcher(), question, sequential=True)
                par, ok = await time_run(make_researcher(), question, sequential=False)
                seq_times.append(seq)
                par_times.append(par)
                ok_counts.append(ok)
            rows.append(
                (question, statistics.mean(seq_times), statistics.mean(par_times), min(ok_counts))
            )

    label_width = 58
    print()
    print(f"{'Question':<{label_width}} {'Sequential':>10} {'Parallel':>9} {'Speedup':>8} {'Sources':>8}")
    print("-" * (label_width + 40))
    for question, seq, par, ok in rows:
        label = question if len(question) <= label_width else question[: label_width - 3] + "..."
        speedup = seq / par if par > 0 else float("inf")
        print(f"{label:<{label_width}} {seq:>9.2f}s {par:>8.2f}s {speedup:>7.2f}x {ok:>5}/3")

    total_seq = sum(seq for _, seq, _, _ in rows)
    total_par = sum(par for _, _, par, _ in rows)
    print("-" * (label_width + 40))
    print(
        f"{'TOTAL (' + str(len(rows)) + ' questions, ' + str(runs) + ' run(s) each)':<{label_width}} "
        f"{total_seq:>9.2f}s {total_par:>8.2f}s {total_seq / total_par:>7.2f}x"
    )
    print(
        f"\nmode={'offline' if offline else 'live'} | semaphore={settings.max_concurrent_requests} "
        f"| per-source timeout={settings.per_source_timeout_seconds}s | cache=disabled for both modes"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true",
                        help="Use the offline AI service (no API keys, no network).")
    parser.add_argument("--runs", type=int, default=1,
                        help="Repetitions per question, averaged (default: 1).")
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be >= 1")

    setup_logging("WARNING")  # keep the table clean; failures still surface
    load_dotenv()             # ai/ reads provider selection + keys from os.environ
    asyncio.run(bench(offline=args.offline, runs=args.runs))


if __name__ == "__main__":
    main()
