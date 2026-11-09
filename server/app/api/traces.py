"""Read API for traces. A "trace" is not stored anywhere: it's computed from its spans.

GET /v1/traces            list with filters + cursor pagination (newest first)
GET /v1/traces/{trace_id} all spans of one trace, flat (the dashboard builds the tree)
"""

import base64
import json
from datetime import datetime
from typing import Annotated, Any, Literal, LiteralString
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import AwareDatetime, BaseModel, JsonValue

from app.auth import require_project
from app.db import get_pool

router = APIRouter(prefix="/v1")

Project = Annotated[UUID, Depends(require_project)]
Pool = Annotated[AsyncConnectionPool, Depends(get_pool)]


class TraceSummary(BaseModel):
    trace_id: UUID
    name: str
    start_time: datetime
    end_time: datetime
    duration_ms: float
    status: Literal["ok", "error"]
    has_root: bool  # False while only children have arrived (root span still running/in flight)
    span_count: int
    error_count: int
    models: list[str]
    total_cost_usd: float | None
    input_tokens: int | None
    output_tokens: int | None


class TraceList(BaseModel):
    traces: list[TraceSummary]
    next_cursor: str | None  # pass as ?cursor= to get the next page; None = last page


class SpanOut(BaseModel):
    span_id: UUID
    parent_span_id: UUID | None
    name: str
    kind: str
    start_time: datetime
    end_time: datetime
    duration_ms: float
    status: str
    error_message: str | None
    model: str | None
    input: JsonValue
    output: JsonValue
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    metadata: JsonValue


class TraceDetail(BaseModel):
    trace_id: UUID
    spans: list[SpanOut]


# Trace status = the root span's status (a debate that recovered from a failed tool call
# is "ok"). If the root hasn't arrived yet, fall back to "any span failed".
# `name` is the root's name, or the earliest span's name while the root is missing.
TRACES_SQL_HEAD: LiteralString = """
WITH per_trace AS (
  SELECT
    trace_id,
    min(start_time) AS start_time,
    max(end_time) AS end_time,
    count(*) AS span_count,
    count(*) FILTER (WHERE status = 'error') AS error_count,
    (array_agg(name ORDER BY start_time) FILTER (WHERE parent_span_id IS NULL))[1] AS root_name,
    (array_agg(status ORDER BY start_time) FILTER (WHERE parent_span_id IS NULL))[1] AS root_status,
    (array_agg(name ORDER BY start_time))[1] AS first_name,
    array_remove(array_agg(DISTINCT model), NULL) AS models,
    sum(cost_usd) AS total_cost_usd,
    sum(input_tokens) AS input_tokens,
    sum(output_tokens) AS output_tokens
  FROM spans
  WHERE project_id = %(project_id)s
  GROUP BY trace_id
), traces AS (
  SELECT
    trace_id,
    coalesce(root_name, first_name) AS name,
    start_time,
    end_time,
    extract(epoch FROM end_time - start_time) * 1000 AS duration_ms,
    coalesce(root_status, CASE WHEN error_count > 0 THEN 'error' ELSE 'ok' END) AS status,
    root_name IS NOT NULL AS has_root,
    span_count, error_count, models, total_cost_usd, input_tokens, output_tokens
  FROM per_trace
)
SELECT * FROM traces
WHERE """

TRACES_SQL_TAIL: LiteralString = """
ORDER BY start_time DESC, trace_id DESC
LIMIT %(limit)s
"""


def encode_cursor(start_time: datetime, trace_id: UUID) -> str:
    raw = json.dumps([start_time.isoformat(), str(trace_id)]).encode()
    return base64.urlsafe_b64encode(raw).decode()


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        start, trace_id = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return datetime.fromisoformat(start), UUID(trace_id)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid cursor") from None


@router.get("/traces", response_model=TraceList)
async def list_traces(
    project_id: Project,
    pool: Pool,
    status: Literal["ok", "error"] | None = None,
    model: str | None = None,
    min_cost: Annotated[float | None, Query(ge=0)] = None,
    min_duration_ms: Annotated[float | None, Query(ge=0)] = None,
    start: AwareDatetime | None = None,  # trace started at or after
    end: AwareDatetime | None = None,  # trace started before
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
) -> TraceList:
    params: dict[str, Any] = {"project_id": project_id, "limit": limit + 1}
    conditions: list[LiteralString] = ["TRUE"]
    if status:
        conditions.append("status = %(status)s")
        params["status"] = status
    if model:
        conditions.append("%(model)s = ANY(models)")
        params["model"] = model
    if min_cost is not None:
        conditions.append("total_cost_usd >= %(min_cost)s")
        params["min_cost"] = min_cost
    if min_duration_ms is not None:
        conditions.append("duration_ms >= %(min_duration_ms)s")
        params["min_duration_ms"] = min_duration_ms
    if start:
        conditions.append("start_time >= %(start)s")
        params["start"] = start
    if end:
        conditions.append("start_time < %(end)s")
        params["end"] = end
    if cursor:
        # Keyset pagination: continue strictly after the last row of the previous page.
        # Stable even while new traces arrive (unlike OFFSET, which would shift rows).
        conditions.append("(start_time, trace_id) < (%(c_start)s, %(c_trace)s)")
        params["c_start"], params["c_trace"] = decode_cursor(cursor)

    sql = TRACES_SQL_HEAD + " AND ".join(conditions) + TRACES_SQL_TAIL
    async with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(sql, params)
        rows = await cur.fetchall()

    # We asked for limit+1 rows: if the extra one exists, there is another page.
    page = [TraceSummary.model_validate(r) for r in rows[:limit]]
    next_cursor = (
        encode_cursor(page[-1].start_time, page[-1].trace_id) if len(rows) > limit else None
    )
    return TraceList(traces=page, next_cursor=next_cursor)


@router.get("/traces/{trace_id}", response_model=TraceDetail)
async def get_trace(trace_id: UUID, project_id: Project, pool: Pool) -> TraceDetail:
    async with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id AS span_id, parent_span_id, name, kind, start_time, end_time,
                   extract(epoch FROM end_time - start_time) * 1000 AS duration_ms,
                   status, error_message, model, input, output,
                   input_tokens, output_tokens, cost_usd, metadata
            FROM spans
            WHERE project_id = %s AND trace_id = %s
            ORDER BY start_time, id
            """,
            (project_id, trace_id),
        )
        rows = await cur.fetchall()
    if not rows:  # unknown trace, or another project's trace: same answer
        raise HTTPException(status_code=404, detail="trace not found")
    return TraceDetail(trace_id=trace_id, spans=[SpanOut.model_validate(r) for r in rows])
