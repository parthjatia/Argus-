"""Writing spans to Postgres. Used by /v1/ingest now and by the Redis worker later."""

from collections.abc import Sequence
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from app.schemas import SpanIn

# ON CONFLICT (project_id, id) DO NOTHING makes the insert idempotent: a span that is
# already stored (a retry, or a duplicate inside the batch) is skipped silently, and
# the first stored version wins. cost_usd stays NULL until the worker computes it.
INSERT_SPAN_SQL = """
INSERT INTO spans (
  project_id, id, trace_id, parent_span_id, name, kind, start_time, end_time,
  status, error_message, model, input, output, input_tokens, output_tokens, metadata
) VALUES (
  %s, %s, %s, %s, %s, %s, %s, %s,
  %s, %s, %s, %s, %s, %s, %s, %s
)
ON CONFLICT (project_id, id) DO NOTHING
"""


def _jsonb(value: object) -> Jsonb | None:
    # Python None → SQL NULL (not the JSON value `null`).
    return None if value is None else Jsonb(value)


async def store_spans(conn: AsyncConnection, project_id: UUID, spans: Sequence[SpanIn]) -> int:
    """Insert spans in one transaction. Returns how many rows were actually new."""
    params = [
        (
            project_id,
            s.span_id,
            s.trace_id,
            s.parent_span_id,
            s.name,
            s.kind,
            s.start_time,
            s.end_time,
            s.status,
            s.error_message,
            s.model,
            _jsonb(s.input),
            _jsonb(s.output),
            s.input_tokens,
            s.output_tokens,
            _jsonb(s.metadata),
        )
        for s in spans
    ]
    # All or nothing: if any row fails, the whole batch is rolled back.
    async with conn.transaction():
        async with conn.cursor() as cur:
            await cur.executemany(INSERT_SPAN_SQL, params)
            return cur.rowcount  # rows inserted; skipped duplicates count 0
