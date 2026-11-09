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


@pytest.fixture
def migrated_db(empty_db: str) -> str:
    apply_migrations(empty_db)
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
