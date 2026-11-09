"""Argus ingestion + read API."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Literal

import psycopg
import redis.asyncio as aioredis
from fastapi import FastAPI, Response
from pydantic import BaseModel

from app.config import get_settings

logger = logging.getLogger("argus.server")

CHECK_TIMEOUT_S = 2.0

app = FastAPI(title="Argus", version="0.1.0")

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
