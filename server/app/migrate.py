"""Apply plain-SQL migrations from server/migrations/ in filename order, each exactly once.

Run with:  python -m app.migrate   (or `make migrate`)
"""

from pathlib import Path

import psycopg

from app.config import get_settings

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

# Arbitrary constant. Two `make migrate` runs at the same time would otherwise
# both see a file as "not applied" and both try to run it.
ADVISORY_LOCK_ID = 727_001


def apply_migrations(conninfo: str, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations; return the filenames applied by this call."""
    files = sorted(migrations_dir.glob("*.sql"))  # 0001_…, 0002_… → lexical order
    applied_now: list[str] = []

    # autocommit=True: we manage transactions explicitly, one per migration file.
    with psycopg.connect(conninfo, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (ADVISORY_LOCK_ID,))
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                  filename TEXT PRIMARY KEY,
                  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            done = {row[0] for row in conn.execute("SELECT filename FROM schema_migrations")}

            for path in files:
                if path.name in done:
                    continue
                # The file and its bookkeeping row commit together, or neither does.
                # A failing file leaves no half-created tables and is retried next run.
                with conn.transaction():
                    conn.execute(path.read_text(encoding="utf-8"))
                    conn.execute(
                        "INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,)
                    )
                applied_now.append(path.name)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_ID,))

    return applied_now


def main() -> None:
    applied = apply_migrations(get_settings().database_url)
    if applied:
        for name in applied:
            print(f"applied {name}")
    else:
        print("database is up to date")


if __name__ == "__main__":
    main()
