"""Endpoints added for the control-room dashboard: live traces, recent spans, range metrics,
breakdown by attribute, and pipeline status."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import SpanIn
from app.seed import create_project_key
from app.storage import store_spans
from app.streams import STREAM
from worker.consumer import Worker


@dataclass
class Dash:
    client: TestClient
    db: str
    key: str
    project_id: UUID
    worker: Worker

    def get(self, path: str, **params: Any) -> Any:
        res = self.client.get(path, params=params, headers={"X-API-Key": self.key})
        assert res.status_code == 200, res.text
        return res.json()

    def store(self, spans: list[SpanIn]) -> None:
        with psycopg.connect(self.db) as conn:
            store_spans(conn, [(self.project_id, s) for s in spans])

    def sql(self, query: str, params: tuple = ()) -> None:
        with psycopg.connect(self.db) as conn:
            conn.execute(query, params)  # type: ignore[arg-type]


@pytest.fixture
def dash(migrated_db: str, make_worker, monkeypatch: pytest.MonkeyPatch) -> Iterator[Dash]:
    monkeypatch.setenv("DATABASE_URL", migrated_db)
    project_id, key = create_project_key(migrated_db, "dash")
    worker = make_worker()  # sets REDIS_URL to the test Redis DB (15)
    with TestClient(app) as client:
        yield Dash(client, migrated_db, key, project_id, worker)


NOW = datetime.now(UTC)


def span(trace: UUID, name: str, start: float, dur: float = 1.0, parent: UUID | None = None,
         **kw: Any) -> SpanIn:  # fmt: skip
    return SpanIn(
        span_id=kw.pop("span_id", uuid4()), trace_id=trace, parent_span_id=parent, name=name,
        kind=kw.pop("kind", "span"), start_time=NOW + timedelta(seconds=start),
        end_time=NOW + timedelta(seconds=start + dur), status=kw.pop("status", "ok"), **kw,
    )  # fmt: skip


def debate_children(trace: UUID, root: UUID, start: float = -10) -> list[SpanIn]:
    common = {"topic": "Cats vs dogs", "rounds": 3, "pro_model": "test-small"}
    return [
        span(trace, "debate.turn", start, 1, root, metadata=common | {"round": 1, "role": "pro"}),
        span(
            trace,
            "openai.chat.completions",
            start + 1.5,
            1,
            root,
            kind="generation",
            model="test-small",
            input_tokens=100,
            output_tokens=50,
            metadata=common | {"round": 2, "role": "con"},
        ),  # fmt: skip
    ]


# --- live --------------------------------------------------------------------------------


def test_live_lists_traces_without_root_and_recent_activity(dash: Dash) -> None:
    running, root_id = uuid4(), uuid4()
    finished, f_root = uuid4(), uuid4()
    stale, s_root = uuid4(), uuid4()
    dash.store(debate_children(running, root_id))
    dash.store(debate_children(finished, f_root) + [span(finished, "debate", -10, 9,
                                                         span_id=f_root)])  # fmt: skip
    dash.store(debate_children(stale, s_root))
    # A crashed app: its spans arrived long ago and the root never will.
    dash.sql("UPDATE spans SET received_at = now() - interval '5 minutes' WHERE trace_id = %s",
             (stale,))  # fmt: skip

    (t,) = dash.get("/v1/traces/live")
    assert t["trace_id"] == str(running)
    assert t["span_count"] == 2
    assert t["input_tokens"] == 100 and t["output_tokens"] == 50
    assert t["total_cost_usd"] == pytest.approx((100 * 0.10 + 50 * 0.40) / 1e6)
    # Metadata merged across spans, the newest span's values winning.
    assert t["attributes"] == {"topic": "Cats vs dogs", "rounds": 3, "pro_model": "test-small",
                               "round": 2, "role": "con"}  # fmt: skip
    assert t["latest_span"]["name"] == "openai.chat.completions"
    assert t["latest_span"]["kind"] == "generation"

    assert dash.get("/v1/metrics/summary")["running"] == 1
    assert dash.get("/v1/traces/live", window_s=3600)[-1]["trace_id"] in {str(running), str(stale)}


def test_trace_list_has_root_metadata_and_name_filter(dash: Dash) -> None:
    trace, root = uuid4(), uuid4()
    other = uuid4()
    dash.store([span(trace, "debate", -5, 2, span_id=root, metadata={"winner": "pro"}),
                span(other, "chat", -4, 1)])  # fmt: skip
    traces = dash.get("/v1/traces", name="debate")["traces"]
    assert [t["trace_id"] for t in traces] == [str(trace)]
    assert traces[0]["root_metadata"] == {"winner": "pro"}


# --- recent spans ------------------------------------------------------------------------


def test_recent_spans_newest_first_without_payloads(dash: Dash) -> None:
    trace = uuid4()
    dash.store([span(trace, f"s{i}", -10 + i, 0.5, input={"big": "x" * 100}) for i in range(5)])
    rows = dash.get("/v1/spans/recent", limit=3)
    assert [r["name"] for r in rows] == ["s4", "s3", "s2"]
    assert "input" not in rows[0] and "output" not in rows[0]
    assert rows[0]["trace_id"] == str(trace)


# --- range metrics -----------------------------------------------------------------------


def test_summary_counts_only_the_range(dash: Dash) -> None:
    t1, r1 = uuid4(), uuid4()
    dash.store([span(t1, "debate", -60, 2, span_id=r1, status="error"),
                span(t1, "llm", -59, 1, r1, kind="generation", model="test-small",
                     input_tokens=1000, output_tokens=1000)])  # fmt: skip
    old = uuid4()
    dash.store([span(old, "debate", -7200, 1)])  # 2 h ago: outside the default 1 h

    s = dash.get("/v1/metrics/summary")
    assert (s["traces"], s["trace_errors"], s["error_rate"]) == (1, 1, 1.0)
    assert (s["spans"], s["generations"]) == (2, 1)
    assert s["cost_usd"] == pytest.approx(0.0005)
    assert s["p50_ms"] == pytest.approx(1000)
    assert s["spans_last_5m"] == 2
    week = dash.get("/v1/metrics/summary", start=(NOW - timedelta(days=7)).isoformat())
    assert week["traces"] == 2


def test_timeseries_fills_empty_buckets(dash: Dash) -> None:
    t = uuid4()
    dash.store([span(t, "debate", -30, 1, status="error"), span(uuid4(), "debate", -25, 1)])
    start = (NOW - timedelta(minutes=10)).isoformat()
    rows = dash.get("/v1/metrics/timeseries", start=start, bucket_s=60)
    assert len(rows) in (10, 11, 12)  # epoch-aligned minutes covering the range
    busy = [r for r in rows if r["traces"]]
    assert len(busy) in (1, 2)  # both traces, maybe split by a minute boundary
    assert sum(r["traces"] for r in rows) == 2
    assert sum(r["trace_errors"] for r in rows) == 1
    assert all(r["error_rate"] is None for r in rows if not r["traces"])


def test_range_validation(dash: Dash) -> None:
    h = {"X-API-Key": dash.key}
    end = NOW.isoformat()
    assert dash.client.get("/v1/metrics/summary", params={"start": end, "end": end},
                           headers=h).status_code == 422  # fmt: skip
    week = (NOW - timedelta(days=7)).isoformat()
    too_many = dash.client.get("/v1/metrics/timeseries",
                               params={"start": week, "bucket_s": 60}, headers=h)  # fmt: skip
    assert too_many.status_code == 422


def test_cost_by_model_and_model_stats(dash: Dash) -> None:
    t = uuid4()
    gens = [span(t, "llm", -100 + i, 0.1 * (i + 1), kind="generation", model="test-large",
                 input_tokens=1000, output_tokens=0, status="error" if i == 0 else "ok")
            for i in range(4)]  # fmt: skip
    gens.append(span(t, "llm", -50, 0.2, kind="generation", model="free-model"))
    dash.store(gens)

    cost = dash.get("/v1/metrics/cost_by_model", bucket_s=3600)
    by_model = {}
    for row in cost:
        by_model.setdefault(row["model"], []).append(row)
    assert sum(r["calls"] for r in by_model["test-large"]) == 4

    stats = {m["model"]: m for m in dash.get("/v1/metrics/models")}
    large = stats["test-large"]
    assert (large["calls"], large["errors"], large["error_rate"]) == (4, 1, 0.25)
    assert large["avg_ms"] == pytest.approx(250, abs=1)
    assert large["input_tokens"] == 4000
    assert large["avg_cost_usd"] == pytest.approx(0.0008)  # 1000 tokens × $0.80/1M
    assert stats["free-model"]["cost_usd"] is None  # no price: unknown, not $0


def test_breakdown_by_attribute(dash: Dash) -> None:
    t = uuid4()
    dash.store([
        span(t, "llm", -10, 1, kind="generation", model="test-small", input_tokens=1_000_000,
             output_tokens=0, metadata={"role": "pro"}),
        span(t, "llm", -9, 1, kind="generation", model="test-small", input_tokens=2_000_000,
             output_tokens=0, metadata={"role": "judge"}),
        span(t, "llm", -8, 1, kind="generation", model="test-small"),
    ])  # fmt: skip
    rows = dash.get("/v1/metrics/breakdown", attribute="role")
    assert [(r["value"], r["cost_usd"]) for r in rows] == [
        ("judge", pytest.approx(0.2)), ("pro", pytest.approx(0.1)), (None, None)
    ]  # fmt: skip
    bad = dash.client.get("/v1/metrics/breakdown", params={"attribute": "a'; drop"},
                          headers={"X-API-Key": dash.key})  # fmt: skip
    assert bad.status_code == 422


# --- system ------------------------------------------------------------------------------


def test_system_reports_backlog_pending_and_rates(dash: Dash) -> None:
    spans = [
        {"span_id": str(uuid4()), "trace_id": str(uuid4()), "name": "s", "kind": "span",
         "start_time": NOW.isoformat(), "end_time": NOW.isoformat(), "status": "ok"}
        for _ in range(5)
    ]  # fmt: skip
    res = dash.client.post("/v1/ingest", json={"batch": spans}, headers={"X-API-Key": dash.key})
    assert res.status_code == 202

    s = dash.get("/v1/system", minutes=5)
    assert s["postgres"]["ok"] and s["redis"]["ok"]
    assert s["stream"]["length"] == 5
    assert s["stream"]["backlog"] == 5  # nobody has read them yet
    assert s["stream"]["pending"] == 0
    assert len(s["rates"]) == 5
    assert s["rates"][-1]["ingested"] == 5 and s["rates"][-1]["written"] == 0

    # Delivered to a worker but not acknowledged = pending.
    dash.worker.r.xreadgroup("writers", "w", {STREAM: ">"}, count=2)
    s = dash.get("/v1/system")
    assert s["stream"]["pending"] == 2 and s["stream"]["backlog"] == 3
    assert s["stream"]["oldest_pending_ms"] is not None
    assert {c["name"] for c in s["stream"]["consumers"]} >= {"w"}

    dash.worker.claim_stale()  # claim_min_idle_ms=0 in tests: takes over w's 2 messages
    dash.worker.drain()
    s = dash.get("/v1/system")
    assert (s["stream"]["pending"], s["stream"]["backlog"]) == (0, 0)
    assert sum(p["written"] for p in s["rates"]) == 5


def test_system_reports_redis_down_without_failing(dash: Dash, monkeypatch) -> None:
    async def boom(*_: Any, **__: Any) -> None:
        raise ConnectionError("redis down")

    monkeypatch.setattr(app.state.redis, "ping", boom)
    s = dash.get("/v1/system")
    assert s["postgres"]["ok"] is True
    assert s["redis"] == {"ok": False, "latency_ms": None, "error": "unreachable"}
    assert s["stream"] is None


def test_new_endpoints_require_a_key(dash: Dash) -> None:
    for path in ("/v1/traces/live", "/v1/spans/recent", "/v1/metrics/summary", "/v1/system",
                 "/v1/metrics/models"):  # fmt: skip
        assert dash.client.get(path, headers={"X-API-Key": "argus_nope"}).status_code == 401
