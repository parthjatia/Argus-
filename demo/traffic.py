"""Run many real debates to fill the dashboard.

    python traffic.py --n 10 [--concurrency 2]

Each debate runs in its own thread, and each thread starts with an empty context, so
every debate becomes its own trace. The concurrency limit (default 2) keeps CPU-only Ollama
responsive and stays under Groq's free-tier rate limits; a 429 that still happens is
retried by arena.llm.chat (Retry-After / backoff). Failure-injection env vars apply as usual.
"""

import argparse
import random
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import argus
from arena.debate import load_topics, run_debate
from arena.llm import make_clients

DEFAULT_CONCURRENCY = 2


def run_traffic(
    n: int,
    concurrency: int = DEFAULT_CONCURRENCY,
    clients_factory: Callable[[], dict[Any, Any]] = make_clients,
) -> Counter[str]:
    """Run n debates, at most `concurrency` at a time. Returns outcome counts."""
    topics = load_topics()
    outcomes: Counter[str] = Counter()

    def one() -> None:
        run_debate(random.choice(topics), clients=clients_factory())

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = [pool.submit(one) for _ in range(n)]
        for i, future in enumerate(as_completed(futures), 1):
            try:
                future.result()
                outcomes["ok"] += 1
            except Exception as exc:
                outcomes[type(exc).__name__] += 1
            print(f"  {i}/{n} debates done ({dict(outcomes)})", flush=True)
    return outcomes


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate many real debate traces.")
    parser.add_argument("--n", type=int, default=10)
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    args = parser.parse_args()

    argus.init()
    make_clients()  # fail fast on missing config (e.g. GROQ_API_KEY) before starting threads
    start = time.perf_counter()
    outcomes = run_traffic(args.n, args.concurrency)
    elapsed = time.perf_counter() - start
    flushed = argus.flush(timeout=30)
    print(f"\n{args.n} debates in {elapsed:.1f}s: {dict(outcomes)}")
    print(f"exported: {flushed}  stats: {argus.get_stats()}")


if __name__ == "__main__":
    main()
