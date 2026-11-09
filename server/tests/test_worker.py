import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest

from app.schemas import SpanIn
from app.seed import create_project_key
from app.streams import DEAD_LETTER_STREAM, GROUP, STREAM

SERVER_DIR = Path(__file__).resolve().parents[1]


def make_span(**overrides: Any) -> SpanIn:
    now = datetime.now(UTC)
    data: dict[str, Any] = {
        "span_id": uuid4(),
        "trace_id": uuid4(),
        "name": "s",
        "kind": "span",
        "start_time": now,
        "end_time": now + timedelta(milliseconds=5),
        "status": "ok",
    } | overrides
    return SpanIn(**data)


def enqueue(r, project_id: UUID, spans: list[SpanIn]) -> None:
    for s in spans:
        r.xadd(STREAM, {"project_id": str(project_id), "span": s.model_dump_json()})


def count_rows(db: str, sql: str = "SELECT count(*) FROM spans") -> int:
    with psycopg.connect(db) as conn:
        return conn.execute(sql).fetchone()[0]  # type: ignore[index]


def pending(r) -> int:
    return r.xpending(STREAM, GROUP)["pending"]


@pytest.fixture
def project(migrated_db: str) -> UUID:
    return create_project_key(migrated_db, "p")[0]


# --- the happy path ----------------------------------------------------------------------


def test_worker_stores_and_acks(make_worker, migrated_db: str, project: UUID) -> None:
    w = make_worker()
    enqueue(w.r, project, [make_span() for _ in range(1200)])  # > one 500-message batch
    assert w.drain() == 1200
    assert count_rows(migrated_db) == 1200
    assert pending(w.r) == 0


# --- crashes -----------------------------------------------------------------------------


def test_killed_between_commit_and_ack_is_reclaimed_without_duplicates(
    make_worker, migrated_db: str, redis_url: str, project: UUID
) -> None:
    w = make_worker("survivor")
    enqueue(w.r, project, [make_span() for _ in range(50)])

    # A real worker process that hard-exits (like `kill -9`) right after COMMIT,
    # before XACK. Its rows are in Postgres, but Redis still lists them as pending.
    env = os.environ | {
        "DATABASE_URL": migrated_db,
        "REDIS_URL": redis_url,
        "WORKER_NAME": "crashy",
        "ARGUS_WORKER_CRASH_AFTER_COMMIT": "1",
    }
    proc = subprocess.run(
        [sys.executable, "-m", "worker.consumer"], cwd=SERVER_DIR, env=env, timeout=60,
        capture_output=True, text=True,
    )  # fmt: skip
    assert proc.returncode == 1, proc.stderr
    assert count_rows(migrated_db) == 50
    assert pending(w.r) == 50  # committed but never acked

    # Another worker takes the dead consumer's messages over with XAUTOCLAIM.
    assert w.claim_stale() == 50
    assert count_rows(migrated_db) == 50  # ON CONFLICT DO NOTHING: no duplicates
    assert pending(w.r) == 0


def test_killed_before_commit_is_reclaimed_and_stored(make_worker, migrated_db, project) -> None:
    crashed, survivor = make_worker("crashed"), make_worker("survivor")
    enqueue(crashed.r, project, [make_span() for _ in range(10)])
    assert len(crashed.read_new(block_ms=None)) == 10  # delivered... then the process dies

    assert count_rows(migrated_db) == 0
    assert survivor.drain() == 0  # nothing new: the messages belong to `crashed`
    assert survivor.claim_stale() == 10
    assert count_rows(migrated_db) == 10
    assert pending(survivor.r) == 0


def test_claim_waits_for_min_idle_time(make_worker, project) -> None:
    busy = make_worker("busy")
    other = make_worker("other", claim_min_idle_ms=60_000)
    enqueue(busy.r, project, [make_span()])
    busy.read_new(block_ms=None)  # `busy` is still working on it (just delivered)
    assert other.claim_stale() == 0  # not stolen from a live consumer
    assert pending(busy.r) == 1


def test_db_down_means_no_ack_then_retry_succeeds(make_worker, migrated_db, project) -> None:
    w = make_worker(conninfo="postgresql://argus:x@127.0.0.1:1/argus")
    enqueue(w.r, project, [make_span() for _ in range(3)])

    with pytest.raises(psycopg.OperationalError):
        w.handle(w.read_new(block_ms=None))
    assert pending(w.r) == 3  # not acked: Redis still remembers them

    w.conninfo = migrated_db  # Postgres is back
    assert w.handle(w.read_own_pending()) == 3
    assert count_rows(migrated_db) == 3
    assert pending(w.r) == 0


def test_poison_messages_are_dead_lettered_not_retried_forever(
    make_worker, migrated_db, project
) -> None:
    w = make_worker()
    w.r.xadd(STREAM, {"project_id": str(project), "span": "{not json"})
    enqueue(w.r, uuid4(), [make_span(name="unknown-project")])  # FK violation in Postgres
    enqueue(w.r, project, [make_span(name="good")])

    assert w.drain() == 3
    assert count_rows(migrated_db) == 1  # the good one still got in
    assert pending(w.r) == 0
    dead = w.r.xrange(DEAD_LETTER_STREAM)
    assert len(dead) == 2
    assert {"original_id", "reason"} <= set(dead[0][1])


# --- cost ----------------------------------------------------------------------------------


def _cost(db: str, span: SpanIn) -> Decimal | None:
    with psycopg.connect(db) as conn:
        row = conn.execute("SELECT cost_usd FROM spans WHERE id = %s", (span.span_id,)).fetchone()
    assert row is not None
    return row[0]


def test_cost_uses_price_valid_at_start_time(make_worker, migrated_db, project) -> None:
    w = make_worker()
    # Test prices (conftest): test-large = $1.00 / $4.00 per 1M from 2024-01-01,
    # $0.80 / $3.20 from 2026-06-01.
    old = make_span(kind="generation", model="test-large", input_tokens=1000,
                    output_tokens=500, start_time=datetime(2025, 1, 1, tzinfo=UTC),
                    end_time=datetime(2025, 1, 1, 0, 0, 1, tzinfo=UTC))  # fmt: skip
    new = make_span(kind="generation", model="test-large", input_tokens=1000,
                    output_tokens=500, start_time=datetime(2026, 7, 1, tzinfo=UTC),
                    end_time=datetime(2026, 7, 1, 0, 0, 1, tzinfo=UTC))  # fmt: skip
    unknown = make_span(kind="generation", model="mystery-model", input_tokens=1000)
    no_tokens = make_span(kind="generation", model="test-large")
    enqueue(w.r, project, [old, new, unknown, no_tokens])
    w.drain()

    assert _cost(migrated_db, old) == Decimal("0.003000")  # (1000*1 + 500*4) / 1e6
    assert _cost(migrated_db, new) == Decimal("0.002400")  # (1000*0.8 + 500*3.2) / 1e6
    assert _cost(migrated_db, unknown) is None  # no price → unknown, not zero
    assert _cost(migrated_db, no_tokens) is None
