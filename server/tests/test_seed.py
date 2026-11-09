import hashlib

import psycopg

from app.auth import API_KEY_PREFIX, generate_api_key, hash_api_key
from app.seed import create_project_key


def test_generated_keys_are_prefixed_and_unique() -> None:
    a, b = generate_api_key(), generate_api_key()
    assert a.startswith(API_KEY_PREFIX)
    assert a != b
    assert len(a) > 40


def test_hash_is_deterministic_sha256() -> None:
    key = "argus_example"
    assert hash_api_key(key) == hash_api_key(key)
    assert hash_api_key(key) == hashlib.sha256(key.encode()).hexdigest()


def test_seed_stores_only_the_hash(migrated_db: str) -> None:
    project_id, raw_key = create_project_key(migrated_db, "demo")

    with psycopg.connect(migrated_db) as conn:
        rows = conn.execute("SELECT project_id, key_hash, revoked_at FROM api_keys").fetchall()
        # The raw key must not appear anywhere in the table.
        leaked = conn.execute(
            "SELECT count(*) FROM api_keys a WHERE a::text LIKE %s", (f"%{raw_key}%",)
        ).fetchone()

    assert rows == [(project_id, hash_api_key(raw_key), None)]
    assert leaked == (0,)


def test_seed_twice_reuses_project_and_adds_a_key(migrated_db: str) -> None:
    pid1, key1 = create_project_key(migrated_db, "demo")
    pid2, key2 = create_project_key(migrated_db, "demo")

    assert pid1 == pid2
    assert key1 != key2
    with psycopg.connect(migrated_db) as conn:
        counts = conn.execute(
            "SELECT (SELECT count(*) FROM projects), (SELECT count(*) FROM api_keys)"
        ).fetchone()
    assert counts == (1, 2)
