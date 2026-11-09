from datetime import UTC, datetime
from uuid import uuid4

import psycopg

from app.migrate import MIGRATIONS_DIR, apply_migrations


def test_each_migration_is_applied_once(empty_db: str) -> None:
    all_files = sorted(p.name for p in MIGRATIONS_DIR.glob("*.sql"))

    assert apply_migrations(empty_db) == all_files
    assert apply_migrations(empty_db) == []  # second run is a no-op

    with psycopg.connect(empty_db) as conn:
        tables = {
            r[0]
            for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        }
    assert {"projects", "api_keys", "spans", "model_prices", "schema_migrations"} <= tables


def _insert_span(conn: psycopg.Connection, project_id, span_id, name: str) -> int:
    now = datetime.now(UTC)
    cur = conn.execute(
        """
        INSERT INTO spans (project_id, id, trace_id, name, kind, start_time, end_time, status)
        VALUES (%s, %s, %s, %s, 'span', %s, %s, 'ok')
        ON CONFLICT (project_id, id) DO NOTHING
        """,
        (project_id, span_id, uuid4(), name, now, now),
    )
    return cur.rowcount


def test_span_ids_are_scoped_per_project(migrated_db: str) -> None:
    project_a, project_b, span_id = uuid4(), uuid4(), uuid4()
    with psycopg.connect(migrated_db) as conn:
        for pid in (project_a, project_b):
            conn.execute("INSERT INTO projects (id, name) VALUES (%s, 'p')", (pid,))

        assert _insert_span(conn, project_a, span_id, "a-original") == 1
        # Same span ID in another project: stored, not blocked by project A's row.
        assert _insert_span(conn, project_b, span_id, "b") == 1
        # Same span ID again in project A: duplicate, ignored, original kept.
        assert _insert_span(conn, project_a, span_id, "a-duplicate") == 0

        rows = conn.execute(
            "SELECT project_id, name FROM spans WHERE id = %s ORDER BY name", (span_id,)
        ).fetchall()
    assert rows == [(project_a, "a-original"), (project_b, "b")]
