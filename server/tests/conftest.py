import os
from collections.abc import Callable, Iterator
from typing import Any

import psycopg
import pytest
import redis
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.migrate import apply_migrations
from worker.consumer import Worker


@pytest.fixture
def empty_db() -> Iterator[str]:
    """A freshly created, empty `<dbname>_test` database. Never touches the dev DB."""
    dev_url = os.environ["DATABASE_URL"]
    test_name = f"{conninfo_to_dict(dev_url)['dbname']}_test"

    # CREATE/DROP DATABASE can't run inside a transaction → autocommit.
    with psycopg.connect(dev_url, autocommit=True) as conn:
        ident = sql.Identifier(test_name)
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(ident))
        conn.execute(sql.SQL("CREATE DATABASE {}").format(ident))

    yield make_conninfo(dev_url, dbname=test_name)


# Test-only prices, so cost tests don't depend on the real (free) models in the migrations.
# test-large has two versions to check that the price valid at start_time is used.
TEST_PRICES = """
INSERT INTO model_prices (model, input_per_1m_usd, output_per_1m_usd, valid_from) VALUES
  ('test-small', 0.10, 0.40, '2024-01-01T00:00:00Z'),
  ('test-large', 1.00, 4.00, '2024-01-01T00:00:00Z'),
  ('test-large', 0.80, 3.20, '2026-06-01T00:00:00Z')
"""


@pytest.fixture
def migrated_db(empty_db: str) -> str:
    apply_migrations(empty_db)
    with psycopg.connect(empty_db) as conn:
        conn.execute(TEST_PRICES)
    return empty_db


@pytest.fixture
def redis_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Redis DB 15, flushed before each test, so tests never touch the dev stream (DB 0)."""
    base = os.environ["REDIS_URL"].rsplit("/", 1)[0]
    url = f"{base}/15"
    r = redis.Redis.from_url(url)
    r.flushdb()
    r.close()
    monkeypatch.setenv("REDIS_URL", url)
    yield url


@pytest.fixture
def make_worker(migrated_db: str, redis_url: str) -> Iterator[Callable[..., Worker]]:
    clients: list[redis.Redis] = []
    workers: list[Worker] = []

    def make(name: str = "test-worker", conninfo: str | None = None, **kwargs: Any) -> Worker:
        r = redis.Redis.from_url(redis_url, decode_responses=True)
        clients.append(r)
        w = Worker(r, conninfo or migrated_db, name, **({"claim_min_idle_ms": 0} | kwargs))
        w.ensure_group()
        workers.append(w)
        return w

    yield make
    for r in clients:
        r.close()
    for w in workers:
        w.close()
