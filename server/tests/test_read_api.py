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


@dataclass
class Reader:
    client: TestClient
    db: str
    key: str
    project_id: UUID

    def get(self, path: str, key: str | None = None, **params: Any) -> Any:
        return self.client.get(path, params=params, headers={"X-API-Key": key or self.key})

    def store(self, spans: list[SpanIn], project_id: UUID | None = None) -> None:
        with psycopg.connect(self.db) as conn:
            store_spans(conn, [(project_id or self.project_id, s) for s in spans])


@pytest.fixture
def reader(migrated_db: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[Reader]:
    monkeypatch.setenv("DATABASE_URL", migrated_db)
    project_id, key = create_project_key(migrated_db, "reader")
    with TestClient(app) as client:
        yield Reader(client, migrated_db, key, project_id)


NOW = datetime.now(UTC).replace(microsecond=0)


def span(trace: UUID, name: str, start: float, end: float, parent: UUID | None = None,
         **kw: Any) -> SpanIn:  # fmt: skip
    """start/end are seconds relative to NOW."""
    return SpanIn(
        span_id=kw.pop("span_id", uuid4()), trace_id=trace, parent_span_id=parent, name=name,
        kind=kw.pop("kind", "span"), start_time=NOW + timedelta(seconds=start),
        end_time=NOW + timedelta(seconds=end), status=kw.pop("status", "ok"), **kw,
    )  # fmt: skip


def make_trace(
    start: float = 0,
    *,
    status: str = "ok",
    model: str = "test-small",
    child_error: bool = False,
    tokens: int = 1000,
) -> tuple[UUID, list[SpanIn]]:
    trace, root_id = uuid4(), uuid4()
    root = span(trace, "debate", start, start + 2, span_id=root_id, status=status)
    gen = span(trace, "llm", start + 0.5, start + 1.5, root_id, kind="generation", model=model,
               input_tokens=tokens, output_tokens=tokens,
               status="error" if child_error else "ok")  # fmt: skip
    return trace, [gen, root]


# --- auth & isolation --------------------------------------------------------------------


def test_read_endpoints_require_a_key(reader: Reader) -> None:
    for path in ("/v1/traces", f"/v1/traces/{uuid4()}", "/v1/metrics/cost"):
        assert reader.get(path, key="argus_wrong").status_code == 401


def test_other_projects_traces_are_invisible(reader: Reader) -> None:
    other_project, _ = create_project_key(reader.db, "other")
    trace, spans = make_trace()
    reader.store(spans, project_id=other_project)

    assert reader.get("/v1/traces").json()["traces"] == []
    assert reader.get(f"/v1/traces/{trace}").status_code == 404


# --- list --------------------------------------------------------------------------------


def test_list_summarises_each_trace(reader: Reader) -> None:
    trace, spans = make_trace(child_error=True)
    reader.store(spans)

    (t,) = reader.get("/v1/traces").json()["traces"]
    assert t["trace_id"] == str(trace)
    assert t["name"] == "debate"
    assert t["duration_ms"] == pytest.approx(2000)
    assert t["status"] == "ok"  # root succeeded even though a child failed
    assert t["error_count"] == 1
    assert t["span_count"] == 2
    assert t["models"] == ["test-small"]
    assert t["total_cost_usd"] == pytest.approx((1000 * 0.10 + 1000 * 0.40) / 1e6)
    assert t["has_root"] is True


def test_trace_without_root_yet_is_listed(reader: Reader) -> None:
    trace, (gen, _root) = make_trace(child_error=True)
    reader.store([gen])  # child arrived, parent not yet
    (t,) = reader.get("/v1/traces").json()["traces"]
    assert t["has_root"] is False
    assert t["name"] == "llm"
    assert t["status"] == "error"


def test_filters(reader: Reader) -> None:
    ok_trace, s1 = make_trace(0, model="test-small", tokens=10)
    err_trace, s2 = make_trace(-10, status="error", model="test-large", tokens=100_000)
    reader.store(s1 + s2)

    def ids(**params: Any) -> list[str]:
        return [t["trace_id"] for t in reader.get("/v1/traces", **params).json()["traces"]]

    assert ids() == [str(ok_trace), str(err_trace)]  # newest first
    assert ids(status="error") == [str(err_trace)]
    assert ids(model="test-large") == [str(err_trace)]
    assert ids(min_cost=0.01) == [str(err_trace)]
    assert ids(min_duration_ms=2500) == []
    assert ids(start=(NOW - timedelta(seconds=5)).isoformat()) == [str(ok_trace)]
    assert ids(end=(NOW - timedelta(seconds=5)).isoformat()) == [str(err_trace)]
    assert reader.get("/v1/traces", status="maybe").status_code == 422


def test_cursor_pagination_visits_every_trace_once(reader: Reader) -> None:
    traces = []
    for i in range(7):
        trace, spans = make_trace(-i)
        traces.append(str(trace))
        reader.store(spans)

    seen, cursor, pages = [], None, 0
    while True:
        params = {"limit": 3} | ({"cursor": cursor} if cursor else {})
        body = reader.get("/v1/traces", **params).json()
        seen += [t["trace_id"] for t in body["traces"]]
        pages += 1
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert seen == traces  # newest first, no gaps, no repeats
    assert pages == 3
    assert reader.get("/v1/traces", cursor="garbage").status_code == 400


# --- detail ------------------------------------------------------------------------------


def test_detail_returns_all_spans_flat(reader: Reader) -> None:
    trace, spans = make_trace()
    reader.store(spans)
    body = reader.get(f"/v1/traces/{trace}").json()
    assert [s["name"] for s in body["spans"]] == ["debate", "llm"]  # ordered by start time
    llm = body["spans"][1]
    assert llm["parent_span_id"] == body["spans"][0]["span_id"]
    assert llm["duration_ms"] == pytest.approx(1000)
    assert llm["cost_usd"] is not None


# --- metrics -----------------------------------------------------------------------------


def test_cost_metrics_per_day_and_model(reader: Reader) -> None:
    reader.store(make_trace(model="test-small")[1] + make_trace(-1, model="test-large")[1])
    rows = reader.get("/v1/metrics/cost").json()
    by_model = {r["model"]: r for r in rows}
    assert set(by_model) == {"test-small", "test-large"}
    assert by_model["test-large"]["calls"] == 1
    assert by_model["test-large"]["cost_usd"] == pytest.approx((1000 * 0.8 + 1000 * 3.2) / 1e6)


def test_latency_percentiles(reader: Reader) -> None:
    trace = uuid4()
    reader.store([span(trace, "g", 0, d / 1000, kind="generation", model="m")
                  for d in range(10, 1010, 10)])  # 100 calls: 10ms ... 1000ms  # fmt: skip
    (row,) = reader.get("/v1/metrics/latency").json()
    assert row["calls"] == 100
    assert row["p50_ms"] == pytest.approx(505, abs=1)
    assert row["p95_ms"] == pytest.approx(950.5, abs=1)


def test_error_rate_over_time(reader: Reader) -> None:
    reader.store(make_trace()[1] + make_trace(-1, status="error")[1])
    rows = reader.get("/v1/metrics/errors", days=1, bucket="hour").json()
    assert len(rows) >= 24  # empty hours are filled with zeros
    current = [r for r in rows if r["traces"]]
    assert len(current) == 1
    assert current[0]["traces"] == 2
    assert current[0]["trace_errors"] == 1
    assert current[0]["error_rate"] == pytest.approx(0.5)
    assert all(r["error_rate"] is None for r in rows if not r["traces"])
