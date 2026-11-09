import os
from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.migrate import apply_migrations


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
