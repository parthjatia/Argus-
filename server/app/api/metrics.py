"""Aggregate metrics for the dashboard, computed from spans at read time (UTC buckets).

GET /v1/metrics/cost     cost + tokens per day, per model (generation spans)
GET /v1/metrics/latency  p50 / p95 duration per model (generation spans)
GET /v1/metrics/errors   traces and error rate per hour or day

Range-based endpoints (start/end, any bucket size; used by the control-room dashboard):
GET /v1/metrics/summary        totals for a range: traces, errors, cost, tokens, p50/p95
GET /v1/metrics/timeseries     per bucket: traces, error rate, spans, cost (gaps filled)
GET /v1/metrics/cost_by_model  per bucket and model: cost, calls, tokens
GET /v1/metrics/models         per model: calls, errors, avg/p50/p95 latency, tokens, cost
GET /v1/metrics/breakdown      generation cost/tokens grouped by a metadata attribute
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import AwareDatetime, BaseModel

from app.api.traces import LIVE_ACTIVE_SQL
from app.auth import require_project
from app.db import get_pool

router = APIRouter(prefix="/v1/metrics")

Project = Annotated[UUID, Depends(require_project)]
Pool = Annotated[AsyncConnectionPool, Depends(get_pool)]
Days = Annotated[int, Query(ge=1, le=90)]


class CostPoint(BaseModel):
    day: datetime
    model: str
    cost_usd: float | None  # None: no price known for this model
    input_tokens: int
    output_tokens: int
    calls: int


class LatencyRow(BaseModel):
    model: str
    calls: int
    p50_ms: float
    p95_ms: float


class ErrorPoint(BaseModel):
    bucket: datetime
    traces: int
    trace_errors: int
    error_rate: float | None  # trace_errors / traces; None when there were no traces
    spans: int
    span_errors: int


async def _fetch(pool: AsyncConnectionPool, sql: str, params: dict) -> list[dict]:
    async with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(sql, params)  # type: ignore[arg-type]
        return await cur.fetchall()


@router.get("/cost", response_model=list[CostPoint])
async def cost(project_id: Project, pool: Pool, days: Days = 7) -> list[CostPoint]:
    rows = await _fetch(
        pool,
        """
        SELECT date_trunc('day', start_time, 'UTC') AS day,
               coalesce(model, 'unknown') AS model,
               sum(cost_usd) AS cost_usd,
               coalesce(sum(input_tokens), 0) AS input_tokens,
               coalesce(sum(output_tokens), 0) AS output_tokens,
               count(*) AS calls
        FROM spans
        WHERE project_id = %(p)s AND kind = 'generation'
          AND start_time >= now() - make_interval(days => %(days)s)
        GROUP BY 1, 2
        ORDER BY 1, 2
        """,
        {"p": project_id, "days": days},
    )
    return [CostPoint.model_validate(r) for r in rows]


@router.get("/latency", response_model=list[LatencyRow])
async def latency(project_id: Project, pool: Pool, days: Days = 7) -> list[LatencyRow]:
    rows = await _fetch(
        pool,
        """
        SELECT coalesce(model, 'unknown') AS model,
               count(*) AS calls,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY d) AS p50_ms,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY d) AS p95_ms
        FROM (
          SELECT model, extract(epoch FROM end_time - start_time) * 1000 AS d
          FROM spans
          WHERE project_id = %(p)s AND kind = 'generation'
            AND start_time >= now() - make_interval(days => %(days)s)
        ) g
        GROUP BY 1
        ORDER BY 1
        """,
        {"p": project_id, "days": days},
    )
    return [LatencyRow.model_validate(r) for r in rows]


@router.get("/errors", response_model=list[ErrorPoint])
async def errors(
    project_id: Project,
    pool: Pool,
    days: Days = 7,
    bucket: Literal["hour", "day"] = "hour",
) -> list[ErrorPoint]:
    # generate_series fills empty buckets with zeros, so the chart has no misleading gaps.
    rows = await _fetch(
        pool,
        """
        WITH buckets AS (
          SELECT generate_series(
            date_trunc(%(bucket)s, now() - make_interval(days => %(days)s), 'UTC'),
            date_trunc(%(bucket)s, now(), 'UTC'),
            CASE %(bucket)s WHEN 'hour' THEN interval '1 hour' ELSE interval '1 day' END
          ) AS bucket
        ), counts AS (
          SELECT date_trunc(%(bucket)s, start_time, 'UTC') AS bucket,
                 count(*) FILTER (WHERE parent_span_id IS NULL) AS traces,
                 count(*) FILTER (WHERE parent_span_id IS NULL AND status = 'error')
                   AS trace_errors,
                 count(*) AS spans,
                 count(*) FILTER (WHERE status = 'error') AS span_errors
          FROM spans
          WHERE project_id = %(p)s
            AND start_time >= date_trunc(%(bucket)s, now() - make_interval(days => %(days)s), 'UTC')
          GROUP BY 1
        )
        SELECT b.bucket,
               coalesce(c.traces, 0) AS traces,
               coalesce(c.trace_errors, 0) AS trace_errors,
               c.trace_errors::float / nullif(c.traces, 0) AS error_rate,
               coalesce(c.spans, 0) AS spans,
               coalesce(c.span_errors, 0) AS span_errors
        FROM buckets b LEFT JOIN counts c USING (bucket)
        ORDER BY b.bucket
        """,
        {"p": project_id, "days": days, "bucket": bucket},
    )
    return [ErrorPoint.model_validate(r) for r in rows]


# --- range-based endpoints ------------------------------------------------------------------

MAX_BUCKETS = 1500
Start = Annotated[AwareDatetime | None, Query(description="default: 1 hour before end")]
End = Annotated[AwareDatetime | None, Query(description="default: now")]
BucketS = Annotated[int, Query(ge=10, le=86_400, description="bucket size in seconds")]

DURATION_MS = "extract(epoch FROM end_time - start_time) * 1000"


def _range(start: datetime | None, end: datetime | None) -> tuple[datetime, datetime]:
    end = end or datetime.now(UTC)
    start = start or end - timedelta(hours=1)
    if start >= end:
        raise HTTPException(status_code=422, detail="start must be before end")
    return start, end


def _step(start: datetime, end: datetime, bucket_s: int) -> timedelta:
    if (end - start).total_seconds() / bucket_s > MAX_BUCKETS:
        raise HTTPException(status_code=422, detail=f"more than {MAX_BUCKETS} buckets")
    return timedelta(seconds=bucket_s)


class Summary(BaseModel):
    start: datetime
    end: datetime
    traces: int  # finished traces (root span) that started in the range
    trace_errors: int
    error_rate: float | None
    spans: int
    span_errors: int
    generations: int
    cost_usd: float | None
    input_tokens: int
    output_tokens: int
    p50_ms: float | None  # generation latency
    p95_ms: float | None
    spans_last_5m: int  # for "spans per minute", independent of the range
    running: int  # traces in progress now (same rule as /v1/traces/live)


SUMMARY_SQL = f"""
SELECT count(*) FILTER (WHERE parent_span_id IS NULL) AS traces,
       count(*) FILTER (WHERE parent_span_id IS NULL AND status = 'error') AS trace_errors,
       count(*) AS spans,
       count(*) FILTER (WHERE status = 'error') AS span_errors,
       count(*) FILTER (WHERE kind = 'generation') AS generations,
       sum(cost_usd) AS cost_usd,
       coalesce(sum(input_tokens), 0) AS input_tokens,
       coalesce(sum(output_tokens), 0) AS output_tokens,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY {DURATION_MS})
         FILTER (WHERE kind = 'generation') AS p50_ms,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY {DURATION_MS})
         FILTER (WHERE kind = 'generation') AS p95_ms,
       (SELECT count(*) FROM spans
         WHERE project_id = %(p)s AND start_time >= now() - interval '5 minutes')
         AS spans_last_5m,
       (SELECT count(*) FROM ({LIVE_ACTIVE_SQL}) live) AS running
FROM spans
WHERE project_id = %(p)s AND start_time >= %(start)s AND start_time < %(end)s
"""


@router.get("/summary", response_model=Summary)
async def summary(project_id: Project, pool: Pool, start: Start = None, end: End = None) -> Summary:
    start, end = _range(start, end)
    (row,) = await _fetch(
        pool, SUMMARY_SQL, {"p": project_id, "start": start, "end": end, "window": 60}
    )
    rate = row["trace_errors"] / row["traces"] if row["traces"] else None
    return Summary.model_validate(row | {"start": start, "end": end, "error_rate": rate})


class SeriesPoint(BaseModel):
    bucket: datetime
    traces: int
    trace_errors: int
    error_rate: float | None  # None = no traces in the bucket (not 0%)
    spans: int
    span_errors: int
    generations: int
    cost_usd: float | None


@router.get("/timeseries", response_model=list[SeriesPoint])
async def timeseries(
    project_id: Project, pool: Pool, bucket_s: BucketS, start: Start = None, end: End = None
) -> list[SeriesPoint]:
    start, end = _range(start, end)
    step = _step(start, end, bucket_s)
    # date_bin aligns buckets to the epoch, so a bucket has the same bounds in every
    # request: bars stay put while the dashboard refreshes.
    rows = await _fetch(
        pool,
        """
        WITH b AS (
          SELECT generate_series(date_bin(%(step)s, %(start)s, timestamptz 'epoch'),
                                 %(end)s, %(step)s) AS bucket
        ), c AS (
          SELECT date_bin(%(step)s, start_time, timestamptz 'epoch') AS bucket,
                 count(*) FILTER (WHERE parent_span_id IS NULL) AS traces,
                 count(*) FILTER (WHERE parent_span_id IS NULL AND status = 'error')
                   AS trace_errors,
                 count(*) AS spans,
                 count(*) FILTER (WHERE status = 'error') AS span_errors,
                 count(*) FILTER (WHERE kind = 'generation') AS generations,
                 sum(cost_usd) AS cost_usd
          FROM spans
          WHERE project_id = %(p)s
            AND start_time >= date_bin(%(step)s, %(start)s, timestamptz 'epoch')
            AND start_time < %(end)s
          GROUP BY 1
        )
        SELECT b.bucket,
               coalesce(c.traces, 0) AS traces,
               coalesce(c.trace_errors, 0) AS trace_errors,
               c.trace_errors::float / nullif(c.traces, 0) AS error_rate,
               coalesce(c.spans, 0) AS spans,
               coalesce(c.span_errors, 0) AS span_errors,
               coalesce(c.generations, 0) AS generations,
               c.cost_usd
        FROM b LEFT JOIN c USING (bucket)
        ORDER BY b.bucket
        """,
        {"p": project_id, "start": start, "end": end, "step": step},
    )
    return [SeriesPoint.model_validate(r) for r in rows]


class ModelCostPoint(BaseModel):
    bucket: datetime
    model: str
    calls: int
    cost_usd: float | None
    input_tokens: int
    output_tokens: int


@router.get("/cost_by_model", response_model=list[ModelCostPoint])
async def cost_by_model(
    project_id: Project, pool: Pool, bucket_s: BucketS, start: Start = None, end: End = None
) -> list[ModelCostPoint]:
    start, end = _range(start, end)
    step = _step(start, end, bucket_s)
    rows = await _fetch(
        pool,
        """
        SELECT date_bin(%(step)s, start_time, timestamptz 'epoch') AS bucket,
               coalesce(model, 'unknown') AS model,
               count(*) AS calls,
               sum(cost_usd) AS cost_usd,
               coalesce(sum(input_tokens), 0) AS input_tokens,
               coalesce(sum(output_tokens), 0) AS output_tokens
        FROM spans
        WHERE project_id = %(p)s AND kind = 'generation'
          AND start_time >= date_bin(%(step)s, %(start)s, timestamptz 'epoch')
          AND start_time < %(end)s
        GROUP BY 1, 2
        ORDER BY 1, 2
        """,
        {"p": project_id, "start": start, "end": end, "step": step},
    )
    return [ModelCostPoint.model_validate(r) for r in rows]


class ModelStats(BaseModel):
    model: str
    calls: int
    errors: int
    error_rate: float
    avg_ms: float
    p50_ms: float
    p95_ms: float
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    avg_cost_usd: float | None  # per priced call


@router.get("/models", response_model=list[ModelStats])
async def models(
    project_id: Project, pool: Pool, start: Start = None, end: End = None
) -> list[ModelStats]:
    start, end = _range(start, end)
    rows = await _fetch(
        pool,
        f"""
        SELECT coalesce(model, 'unknown') AS model,
               count(*) AS calls,
               count(*) FILTER (WHERE status = 'error') AS errors,
               count(*) FILTER (WHERE status = 'error')::float / count(*) AS error_rate,
               avg({DURATION_MS}) AS avg_ms,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY {DURATION_MS}) AS p50_ms,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY {DURATION_MS}) AS p95_ms,
               coalesce(sum(input_tokens), 0) AS input_tokens,
               coalesce(sum(output_tokens), 0) AS output_tokens,
               sum(cost_usd) AS cost_usd,
               avg(cost_usd) AS avg_cost_usd
        FROM spans
        WHERE project_id = %(p)s AND kind = 'generation'
          AND start_time >= %(start)s AND start_time < %(end)s
        GROUP BY 1
        ORDER BY 1
        """,
        {"p": project_id, "start": start, "end": end},
    )
    return [ModelStats.model_validate(r) for r in rows]


class BreakdownRow(BaseModel):
    value: str | None  # the attribute's value; None = generations without it
    calls: int
    errors: int
    cost_usd: float | None
    input_tokens: int
    output_tokens: int


@router.get("/breakdown", response_model=list[BreakdownRow])
async def breakdown(
    project_id: Project,
    pool: Pool,
    attribute: Annotated[str, Query(pattern=r"^[A-Za-z0-9_.-]{1,64}$")],
    start: Start = None,
    end: End = None,
) -> list[BreakdownRow]:
    """LLM cost grouped by a metadata attribute, e.g. ?attribute=role = cost per debate role."""
    start, end = _range(start, end)
    params: dict[str, Any] = {"p": project_id, "start": start, "end": end, "attr": attribute}
    rows = await _fetch(
        pool,
        """
        SELECT metadata ->> %(attr)s AS value,
               count(*) AS calls,
               count(*) FILTER (WHERE status = 'error') AS errors,
               sum(cost_usd) AS cost_usd,
               coalesce(sum(input_tokens), 0) AS input_tokens,
               coalesce(sum(output_tokens), 0) AS output_tokens
        FROM spans
        WHERE project_id = %(p)s AND kind = 'generation'
          AND start_time >= %(start)s AND start_time < %(end)s
        GROUP BY 1
        ORDER BY sum(cost_usd) DESC NULLS LAST, 1
        """,
        params,
    )
    return [BreakdownRow.model_validate(r) for r in rows]
