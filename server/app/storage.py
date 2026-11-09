"""Writing spans to Postgres (used by the worker)."""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from app.schemas import SpanIn

# - ON CONFLICT (project_id, id) DO NOTHING makes the insert idempotent: a span that is
#   already stored (an SDK retry, or a message redelivered after a worker crash) is
#   skipped silently, and the first stored version wins.
# - cost_usd: the price row for this model that was valid at the span's start_time
#   (latest valid_from <= start_time). No price or no token counts → NULL ("unknown").
INSERT_SPAN_SQL = """
INSERT INTO spans (
  project_id, id, trace_id, parent_span_id, name, kind, start_time, end_time,
  status, error_message, model, input, output, input_tokens, output_tokens, metadata,
  cost_usd
) VALUES (
  %(project_id)s, %(id)s, %(trace_id)s, %(parent_span_id)s, %(name)s, %(kind)s,
  %(start_time)s, %(end_time)s, %(status)s, %(error_message)s, %(model)s,
  %(input)s, %(output)s, %(input_tokens)s, %(output_tokens)s, %(metadata)s,
  (
    SELECT (coalesce(%(input_tokens)s::int, 0) * p.input_per_1m_usd
          + coalesce(%(output_tokens)s::int, 0) * p.output_per_1m_usd) / 1000000
    FROM model_prices p
    WHERE p.model = %(model)s
      AND p.valid_from <= %(start_time)s
      AND (%(input_tokens)s::int IS NOT NULL OR %(output_tokens)s::int IS NOT NULL)
    ORDER BY p.valid_from DESC
    LIMIT 1
  )
)
ON CONFLICT (project_id, id) DO NOTHING
"""


def _jsonb(value: object) -> Jsonb | None:
    # Python None → SQL NULL (not the JSON value `null`).
    return None if value is None else Jsonb(value)


def _params(project_id: UUID, s: SpanIn) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "id": s.span_id,
        "trace_id": s.trace_id,
        "parent_span_id": s.parent_span_id,
        "name": s.name,
        "kind": s.kind,
        "start_time": s.start_time,
        "end_time": s.end_time,
        "status": s.status,
        "error_message": s.error_message,
        "model": s.model,
        "input": _jsonb(s.input),
        "output": _jsonb(s.output),
        "input_tokens": s.input_tokens,
        "output_tokens": s.output_tokens,
        "metadata": _jsonb(s.metadata),
    }


def store_spans(conn: Connection, rows: Sequence[tuple[UUID, SpanIn]]) -> int:
    """Insert (project_id, span) rows. Returns how many were new (duplicates count 0).

    The caller owns the transaction, so it can commit first and acknowledge after.
    """
    with conn.cursor() as cur:
        cur.executemany(INSERT_SPAN_SQL, [_params(pid, s) for pid, s in rows])
        return cur.rowcount
