"""Aggregate metrics for the dashboard, computed from spans at read time (UTC buckets).

GET /v1/metrics/cost     cost + tokens per day, per model (generation spans)
GET /v1/metrics/latency  p50 / p95 duration per model (generation spans)
GET /v1/metrics/errors   traces and error rate per hour or day
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

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
