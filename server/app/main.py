"""Argus ingestion + read API."""

import asyncio
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Literal

import psycopg
import redis.asyncio as aioredis
import redis.exceptions
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.api.ingest import router as ingest_router
from app.api.metrics import router as metrics_router
from app.api.traces import router as traces_router
from app.config import get_settings
from app.db import create_pool

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("argus.server")

CHECK_TIMEOUT_S = 2.0


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    pool = create_pool(settings)
    await pool.open(wait=False)  # don't block startup if Postgres is down
    app.state.pool = pool
    # Connects lazily on first use, so the server also starts while Redis is down.
    app.state.redis = aioredis.from_url(
        settings.redis_url, socket_connect_timeout=2, socket_timeout=2
    )
    try:
        yield
    finally:
        await app.state.redis.aclose()
        await pool.close()


app = FastAPI(title="Argus", version="0.1.0", lifespan=lifespan)
app.include_router(ingest_router)
app.include_router(traces_router)
app.include_router(metrics_router)


@app.exception_handler(psycopg.OperationalError)
async def database_unavailable(request: Request, exc: psycopg.OperationalError) -> JSONResponse:
    # Covers "can't connect" and psycopg_pool.PoolTimeout (a subclass). 503, not 500,
    # tells the SDK this is temporary and worth retrying with backoff.
    logger.error("database unavailable on %s: %r", request.url.path, exc)
    return JSONResponse(status_code=503, content={"detail": "database unavailable"})


@app.exception_handler(redis.exceptions.RedisError)
async def queue_unavailable(request: Request, exc: redis.exceptions.RedisError) -> JSONResponse:
    logger.error("redis unavailable on %s: %r", request.url.path, exc)
    return JSONResponse(status_code=503, content={"detail": "queue unavailable"})


CheckStatus = Literal["ok", "error"]


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    postgres: CheckStatus
    redis: CheckStatus


async def check_postgres() -> None:
    url = get_settings().database_url
    async with await psycopg.AsyncConnection.connect(url, connect_timeout=2) as conn:
        await conn.execute("SELECT 1")


async def check_redis() -> None:
    client = aioredis.from_url(get_settings().redis_url, socket_connect_timeout=2, socket_timeout=2)
    try:
        await client.ping()
    finally:
        await client.aclose()


async def _run_check(name: str, check: Callable[[], Awaitable[None]]) -> CheckStatus:
    try:
        await asyncio.wait_for(check(), timeout=CHECK_TIMEOUT_S)
        return "ok"
    except Exception as exc:  # any failure means "unhealthy", never a 500
        # Log details server-side; don't leak connection errors to callers.
        logger.warning("health check %s failed: %r", name, exc)
        return "error"


@app.get("/health", response_model=HealthResponse)
async def health(response: Response) -> HealthResponse:
    pg, rd = await asyncio.gather(
        _run_check("postgres", check_postgres),
        _run_check("redis", check_redis),
    )
    healthy = pg == "ok" and rd == "ok"
    if not healthy:
        response.status_code = 503
    return HealthResponse(status="ok" if healthy else "degraded", postgres=pg, redis=rd)
