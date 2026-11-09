"""Chaos test: send spans while killing and restarting the worker and the API, then check
that every span that was accepted is stored in Postgres exactly once.

Run from the repo root with the stack up (`docker compose up -d`):

    python loadtest/chaos.py            # or: make chaos

Standard library only, because it runs on the host: it needs `docker compose` to kill
containers, and talks to the API on localhost:8000.

How it works
  * Sender threads POST batches with SDK-style UUIDv4 span IDs. Like the SDK, a sender
    retries the SAME batch (same IDs) on network errors and 5xx until it gets 202, so
    delivery is at-least-once. A request that dies after XADD but before the response
    is retried and creates a duplicate in the stream, which is exactly what we want to
    see absorbed. 10% of accepted batches are also re-sent on purpose.
  * A chaos thread SIGKILLs / restarts the worker and the server on a fixed schedule.
  * Afterwards we wait until the stream is drained (no pending messages, row count
    stable) and compare the set of accepted span IDs with the rows in `spans`.
"""

import argparse
import json
import random
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "loadtest" / "results"

# (seconds after start, description, docker compose args)
SCHEDULE: list[tuple[float, str, list[list[str]]]] = [
    (10, "worker SIGKILL", [["kill", "-s", "SIGKILL", "worker"]]),
    (15, "worker start", [["start", "worker"]]),
    (25, "server graceful restart", [["restart", "server"]]),
    (40, "server SIGKILL", [["kill", "-s", "SIGKILL", "server"]]),
    (44, "server start", [["start", "server"]]),
    (52, "worker + server SIGKILL", [["kill", "-s", "SIGKILL", "worker", "server"]]),
    (57, "worker + server start", [["start", "worker", "server"]]),
    (65, "worker graceful restart", [["restart", "worker"]]),
]


def compose(*args: str, check: bool = True) -> str:
    out = subprocess.run(
        ["docker", "compose", *args], cwd=ROOT, capture_output=True, text=True, check=False
    )
    if check and out.returncode != 0:
        raise RuntimeError(f"docker compose {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout


def env_value(name: str) -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f"{name} missing in .env")


def psql(sql: str) -> list[str]:
    user, db = env_value("POSTGRES_USER"), env_value("POSTGRES_DB")
    out = compose("exec", "-T", "postgres", "psql", "-U", user, "-d", db, "-At", "-c", sql)
    return [line for line in out.splitlines() if line]


def redis_pending() -> int:
    out = compose("exec", "-T", "redis", "redis-cli", "XPENDING", "spans", "writers")
    return int(out.split()[0]) if out.strip() else 0


def new_project() -> tuple[str, str]:
    name = f"chaos-{datetime.now(UTC):%Y%m%d-%H%M%S}"
    out = compose("exec", "-T", "server", "python", "-m", "app.seed", name)
    key = next(line.split(": ", 1)[1] for line in out.splitlines() if line.startswith("api key"))
    return name, key


def make_batch(spans_per_batch: int) -> tuple[bytes, list[str]]:
    """One trace per batch: a root span plus children, a third of them LLM generations."""
    trace_id, root_id = str(uuid.uuid4()), str(uuid.uuid4())
    now = datetime.now(UTC)
    spans, ids = [], []
    for i in range(spans_per_batch):
        span_id = root_id if i == 0 else str(uuid.uuid4())
        generation = i % 3 == 1
        start = now + timedelta(milliseconds=10 * i)
        spans.append(
            {
                "span_id": span_id,
                "trace_id": trace_id,
                "parent_span_id": None if i == 0 else root_id,
                "name": "chaos.root" if i == 0 else f"chaos.step{i}",
                "kind": "generation" if generation else "span",
                "start_time": start.isoformat(),
                "end_time": (start + timedelta(milliseconds=8)).isoformat(),
                "status": "ok",
                "model": "fake-small" if generation else None,
                "input": {"prompt": "x" * 200} if generation else {},
                "output": {"text": "y" * 200} if generation else {},
                "input_tokens": 120 if generation else None,
                "output_tokens": 80 if generation else None,
            }
        )
        ids.append(span_id)
    return json.dumps({"batch": spans}).encode(), ids


class Stats:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.accepted: set[str] = set()
        self.requests = self.retries = self.deliberate_resends = 0
        self.network_errors = self.server_errors = 0
        self.client_errors: list[str] = []
        self.timeline: list[tuple[float, int]] = []  # (t, retries so far) for the report


def post(host: str, key: str, body: bytes) -> int:
    req = urllib.request.Request(
        f"{host}/v1/ingest",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "X-API-Key": key},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def sender(host: str, key: str, size: int, stop: threading.Event, st: Stats) -> None:
    while not stop.is_set():
        body, ids = make_batch(size)
        attempt = 0
        while True:  # retry the same batch until accepted (at-least-once)
            try:
                status = post(host, key, body)
            except OSError:  # connection refused/reset, timeout: the server is down
                status = 0
            with st.lock:
                st.requests += 1
                if attempt:
                    st.retries += 1
                if status == 0:
                    st.network_errors += 1
                elif status >= 500:
                    st.server_errors += 1
            if status == 202:
                break
            if 400 <= status < 500:
                with st.lock:
                    st.client_errors.append(str(status))
                return  # a 4xx is a bug in this script, not chaos
            attempt += 1
            time.sleep(min(2.0, 0.1 * 2**attempt) * random.uniform(0.5, 1.0))
        with st.lock:
            st.accepted.update(ids)
        if random.random() < 0.10:  # deliberate duplicate delivery
            try:
                if post(host, key, body) == 202:
                    with st.lock:
                        st.deliberate_resends += 1
            except OSError:
                pass
        time.sleep(0.05)


def chaos(t0: float, events: list[tuple[float, str]]) -> None:
    for at, label, commands in SCHEDULE:
        time.sleep(max(0.0, t0 + at - time.monotonic()))
        for cmd in commands:
            compose(*cmd, check=False)
        events.append((round(time.monotonic() - t0, 1), label))
        print(f"  t={events[-1][0]:>5}s  {label}", flush=True)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="http://localhost:8000")
    p.add_argument("--senders", type=int, default=6)
    p.add_argument("--batch", type=int, default=20)
    p.add_argument("--duration", type=float, default=75)
    p.add_argument("--drain-timeout", type=float, default=180)
    args = p.parse_args()

    project, key = new_project()
    print(f"project {project}: {args.senders} senders x {args.batch} spans/batch, "
          f"{args.duration:.0f} s with restarts")  # fmt: skip

    st, stop, events = Stats(), threading.Event(), []
    t0 = time.monotonic()
    threads = [
        threading.Thread(target=sender, args=(args.host, key, args.batch, stop, st), daemon=True)
        for _ in range(args.senders)
    ]
    for t in threads:
        t.start()
    chaos_thread = threading.Thread(target=chaos, args=(t0, events), daemon=True)
    chaos_thread.start()

    time.sleep(args.duration)
    stop.set()
    chaos_thread.join()
    for t in threads:
        t.join(timeout=60)  # each finishes (= gets 202 for) its in-flight batch
    sent_for = time.monotonic() - t0
    compose("start", "worker", "server", check=False)  # in case the schedule left one down

    print(f"sending done after {sent_for:.0f} s; waiting for the worker to drain ...")
    pid = psql(f"SELECT id FROM projects WHERE name = '{project}'")[0]
    deadline, last, stable_since = time.monotonic() + args.drain_timeout, -1, time.monotonic()
    while time.monotonic() < deadline:
        count = int(psql(f"SELECT count(*) FROM spans WHERE project_id = '{pid}'")[0])
        if count != last:
            last, stable_since = count, time.monotonic()
        if count >= len(st.accepted) and redis_pending() == 0:
            break
        if time.monotonic() - stable_since > 90:  # > CLAIM_MIN_IDLE_MS: nothing more comes
            break
        time.sleep(2)
    drained_after = time.monotonic() - t0

    stored = psql(f"SELECT id FROM spans WHERE project_id = '{pid}'")
    stored_set = set(stored)
    missing = st.accepted - stored_set
    unexpected = stored_set - st.accepted
    result = {
        "project": project,
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "senders": args.senders,
        "spans_per_batch": args.batch,
        "send_seconds": round(sent_for, 1),
        "drained_after_seconds": round(drained_after, 1),
        "chaos_events": events,
        "requests": st.requests,
        "retried_requests": st.retries,
        "network_errors": st.network_errors,
        "server_5xx": st.server_errors,
        "client_4xx": len(st.client_errors),
        "deliberate_duplicate_batches": st.deliberate_resends,
        "spans_accepted_unique": len(st.accepted),
        "rows_stored": len(stored),
        "distinct_ids_stored": len(stored_set),
        "missing": len(missing),
        "unexpected": len(unexpected),
        "duplicates": len(stored) - len(stored_set),
        "redis_pending_after": redis_pending(),
    }
    result["passed"] = (
        result["missing"] == 0
        and result["unexpected"] == 0
        and result["duplicates"] == 0
        and result["client_4xx"] == 0
    )
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "chaos.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({k: v for k, v in result.items() if k != "chaos_events"}, indent=2))
    print("PASS: every accepted span stored exactly once" if result["passed"] else "FAIL")
    sys.exit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
