"""Create (or reuse) a demo project and print a NEW API key for it.

Run with:  python -m app.seed [project-name]   (or `make seed`)

The raw key is printed once and never stored; only its hash goes into the DB.
Running it again reuses the project and issues an additional key.
"""

import sys
from uuid import UUID, uuid4

import psycopg

from app.auth import generate_api_key, hash_api_key
from app.config import get_settings


def create_project_key(conninfo: str, project_name: str = "demo") -> tuple[UUID, str]:
    """Return (project_id, raw_api_key)."""
    raw_key = generate_api_key()

    # One transaction: the psycopg connection context manager commits on success
    # and rolls back on error, so we never end up with a project but no key.
    with psycopg.connect(conninfo) as conn:
        row = conn.execute(
            "SELECT id FROM projects WHERE name = %s ORDER BY created_at LIMIT 1",
            (project_name,),
        ).fetchone()
        if row is not None:
            project_id: UUID = row[0]
        else:
            project_id = uuid4()
            conn.execute(
                "INSERT INTO projects (id, name) VALUES (%s, %s)", (project_id, project_name)
            )

        conn.execute(
            "INSERT INTO api_keys (id, project_id, key_hash) VALUES (%s, %s, %s)",
            (uuid4(), project_id, hash_api_key(raw_key)),
        )

    return project_id, raw_key


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "demo"
    project_id, raw_key = create_project_key(get_settings().database_url, name)
    print(f"project: {name} ({project_id})")
    print(f"api key: {raw_key}")
    print("Save this key now: only its hash is stored, it cannot be shown again.")


if __name__ == "__main__":
    main()
