"""Run many debates in parallel to fill the dashboard.

    python traffic.py --n 50 [--concurrency 8]

Each debate runs in its own thread, and each thread starts with an empty context, so
every debate becomes its own trace. Failure-injection env vars apply as usual.
"""

import argparse
import random
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import argus
from arena.debate import load_topics, run_debate
from arena.llm import default_model, provider

MODEL_CHOICES = {
    "fake": ["fake-small", "fake-large"],
}


def pick_models() -> tuple[str, str, str]:
    choices = MODEL_CHOICES.get(provider())
    if not choices:  # real providers: use the configured models
        return default_model("pro"), default_model("con"), default_model("judge")
    return random.choice(choices), random.choice(choices), "fake-large"


def run_traffic(n: int, concurrency: int = 8) -> Counter[str]:
    topics = load_topics()
    outcomes: Counter[str] = Counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(run_debate, random.choice(topics), *pick_models()) for _ in range(n)]
        for i, future in enumerate(as_completed(futures), 1):
            try:
                future.result()
                outcomes["ok"] += 1
            except Exception as exc:
                outcomes[type(exc).__name__] += 1
            if i % 10 == 0 or i == n:
                print(f"  {i}/{n} debates done")
    return outcomes


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate many debate traces.")
    parser.add_argument("--n", type=int, default=50)
    parser.add_argument("--concurrency", type=int, default=8)
    args = parser.parse_args()

    argus.init()
    start = time.perf_counter()
    outcomes = run_traffic(args.n, args.concurrency)
    elapsed = time.perf_counter() - start
    flushed = argus.flush(timeout=30)
    print(f"\n{args.n} debates in {elapsed:.1f}s: {dict(outcomes)}")
    print(f"exported: {flushed}  stats: {argus.get_stats()}")


if __name__ == "__main__":
    main()
