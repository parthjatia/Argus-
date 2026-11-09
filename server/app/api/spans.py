"""GET /v1/spans/recent: the newest finished spans of the project (the dashboard's live feed).

Spans are listed without input/output (those can be 32 KB each); open the trace for them.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, JsonValue

from app.auth import require_project
from app.db import get_pool

router = APIRouter(prefix="/v1")


class RecentSpan(BaseModel):
    span_id: UUID
    trace_id: UUID
    parent_span_id: UUID | None
    name: str
    kind: str
    status: str
    error_message: str | None
    model: str | None
    start_time: datetime
    end_time: datetime
    duration_ms: float
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    metadata: JsonValue


@router.get("/spans/recent", response_model=list[RecentSpan])
async def recent_spans(
    project_id: Annotated[UUID, Depends(require_project)],
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[RecentSpan]:
    # Newest by end time (when the span finished). The start_time bound keeps the scan on
    # the (project_id, start_time) index; spans running longer than 6 h are left out.
    async with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id AS span_id, trace_id, parent_span_id, name, kind, status, error_message,
                   model, start_time, end_time,
                   extract(epoch FROM end_time - start_time) * 1000 AS duration_ms,
                   input_tokens, output_tokens, cost_usd, metadata
            FROM spans
            WHERE project_id = %s AND start_time >= now() - interval '6 hours'
            ORDER BY end_time DESC, id
            LIMIT %s
            """,
            (project_id, limit),
        )
        rows = await cur.fetchall()
    return [RecentSpan.model_validate(r) for r in rows]
