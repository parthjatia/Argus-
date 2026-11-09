"""Load test driver: creates a fresh project + key, runs k6 (ingest.js) in Docker against
the API, then measures how fast the worker drains the stream into Postgres. Spans are
synthetic (ingest.js builds them); no LLM is called.

    python loadtest/run_load.py [--vus 20] [--duration 30s] [--batch 100]   # or: make load

Two numbers matter and they are different things:
  * ingest throughput / latency: what clients see (202 after XADD to Redis)
  * drain throughput: how fast the worker gets spans into Postgres (end-to-end capacity)
Results go to loadtest/results/load-<vus>vu-<batch>.json.
"""

import argparse
import json
import subprocess
import time
from datetime import UTC, datetime

from chaos import RESULTS, ROOT, compose, psql, redis_pending

K6_IMAGE = "grafana/k6:0.54.0"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--vus", type=int, default=20)
    p.add_argument("--duration", default="30s")
    p.add_argument("--batch", type=int, default=100)
    args = p.parse_args()

    project = f"load-{datetime.now(UTC):%Y%m%d-%H%M%S}"
    out = compose("exec", "-T", "server", "python", "-m", "app.seed", project)
    key = next(line.split(": ", 1)[1] for line in out.splitlines() if line.startswith("api key"))
    pid = psql(f"SELECT id FROM projects WHERE name = '{project}'")[0]
    RESULTS.mkdir(parents=True, exist_ok=True)
    api_processes = compose("exec", "-T", "server", "printenv", "WEB_CONCURRENCY", check=False)
    api_processes = api_processes.strip() or "1"
    tag = f"{api_processes}api-{args.vus}vu-{args.batch}"
    summary = f"k6-{tag}.json"

    # k6 joins the compose network and calls the server by its service name.
    print(f"k6: {args.vus} VUs x {args.batch} spans/batch for {args.duration} ...", flush=True)
    subprocess.run(
        ["docker", "run", "--rm", "--network", "argus_default",
         "-v", f"{ROOT / 'loadtest'}:/scripts",
         "-e", f"ARGUS_API_KEY={key}", "-e", "HOST=http://server:8000",
         "-e", f"VUS={args.vus}", "-e", f"DURATION={args.duration}", "-e", f"BATCH={args.batch}",
         K6_IMAGE, "run", "--quiet", "--summary-export", f"/scripts/results/{summary}",
         "/scripts/ingest.js"],
        check=True,
    )  # fmt: skip
    k6_done = time.monotonic()

    # Drain: wait until the worker has stored everything that was accepted.
    k6 = json.loads((RESULTS / summary).read_text(encoding="utf-8"))
    accepted = int(k6["metrics"]["spans_accepted"]["count"])
    at_k6_end = int(psql(f"SELECT count(*) FROM spans WHERE project_id = '{pid}'")[0])
    while True:
        stored = int(psql(f"SELECT count(*) FROM spans WHERE project_id = '{pid}'")[0])
        if stored >= accepted and redis_pending() == 0:
            break
        time.sleep(1)
    drained = time.monotonic()
    first, last = psql(
        f"SELECT min(received_at), max(received_at) FROM spans WHERE project_id = '{pid}'"
    )[0].split("|")
    write_s = (datetime.fromisoformat(last) - datetime.fromisoformat(first)).total_seconds()

    d = k6["metrics"]["http_req_duration"]
    reqs = k6["metrics"]["http_reqs"]
    result = {
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "vus": args.vus,
        "spans_per_batch": args.batch,
        "duration": args.duration,
        "requests": int(reqs["count"]),
        "requests_per_s": round(reqs["rate"], 1),
        "spans_accepted": accepted,
        "api_processes": int(api_processes),
        "ingest_spans_per_s": round(k6["metrics"]["spans_accepted"]["rate"], 0),
        "latency_ms": {k: round(d[k], 1) for k in ("med", "p(95)", "p(99)", "max")},
        "failed_checks": k6["metrics"]["checks"]["fails"],
        "stored_when_k6_ended": at_k6_end,
        "stored": stored,
        "worker_spans_per_s": round(stored / write_s, 0) if write_s else None,
        "drain_after_k6_s": round(drained - k6_done, 1),
    }
    (RESULTS / f"load-{tag}.json").write_text(json.dumps(result, indent=2) + "\n", "utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
