"""Postgres connection pool, opened and closed with the FastAPI app (see main.lifespan)."""

from fastapi import Request
from psycopg_pool import AsyncConnectionPool

from app.config import Settings


def create_pool(settings: Settings) -> AsyncConnectionPool:
    # open=False: the pool is opened in the app lifespan. We don't wait for the
    # first connection there, so the server still starts (and /health reports the
    # problem) while Postgres is down; requests then get 503 after `timeout`.
    return AsyncConnectionPool(
        settings.database_url,
        min_size=1,
        max_size=10,
        timeout=settings.db_pool_timeout_s,
        open=False,
    )


def get_pool(request: Request) -> AsyncConnectionPool:
    return request.app.state.pool
