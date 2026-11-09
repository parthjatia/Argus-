"""GET /v1/system: pipeline health for the dashboard's System page.

Postgres and Redis status (with round-trip time), the Redis Stream backlog, messages
delivered to a worker but not yet acknowledged, dead letters, the worker consumers, and
per-minute throughput: spans accepted by the API vs rows written by the worker.

Unlike the rest of the read API this describes the shared pipeline, not one project:
it's a single-tenant, self-hosted tool, and none of it reveals another project's data.
A failing dependency is reported in the body (never a 5xx), except Postgres being down,
which already fails the API key check with 503.
"""

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

import redis.exceptions
from fastapi import APIRouter, Depends, Query
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel
from redis.asyncio import Redis

from app.auth import require_project
from app.db import get_pool
from app.streams import DEAD_LETTER_STREAM, GROUP, STREAM, get_redis, rate_key

logger = logging.getLogger("argus.system")
router = APIRouter(prefix="/v1")

TIMEOUT_S = 2.0


class Dependency(BaseModel):
    ok: bool
    latency_ms: float | None
    error: str | None = None  # short and generic; details go to the server log


class Consumer(BaseModel):
    name: str
    pending: int
    idle_ms: int  # time since this worker last read from the stream


class StreamInfo(BaseModel):
    length: int  # messages in the stream (stored ones too, until MAXLEN trims them)
    backlog: int | None  # not yet delivered to any worker (consumer-group lag)
    pending: int  # delivered to a worker, not yet acknowledged (= not yet committed)
    dead_letters: int
    consumers: list[Consumer]
    oldest_pending_ms: int | None  # age of the oldest unacknowledged message


class RatePoint(BaseModel):
    minute: datetime
    ingested: int  # spans accepted by POST /v1/ingest
    written: int  # new rows inserted by the worker


class SystemStatus(BaseModel):
    checked_at: datetime
    postgres: Dependency
    redis: Dependency
    stream: StreamInfo | None  # None when Redis is unreachable
    rates: list[RatePoint]  # oldest first, the current (incomplete) minute last


def _s(v: Any) -> str:
    return v.decode() if isinstance(v, bytes) else str(v)


async def _timed(coro: Any) -> tuple[Any, float]:
    t0 = time.perf_counter()
    result = await asyncio.wait_for(coro, TIMEOUT_S)
    return result, (time.perf_counter() - t0) * 1000


async def _postgres(pool: AsyncConnectionPool) -> Dependency:
    async def ping() -> None:
        async with pool.connection() as conn:
            await conn.execute("SELECT 1")

    try:
        _, ms = await _timed(ping())
        return Dependency(ok=True, latency_ms=round(ms, 2))
    except Exception as exc:
        logger.warning("system: postgres check failed: %r", exc)
        return Dependency(ok=False, latency_ms=None, error="unreachable")


async def _stream(r: Redis) -> StreamInfo:
    try:
        length = await r.xlen(STREAM)
        groups = await r.xinfo_groups(STREAM)
    except redis.exceptions.ResponseError:  # stream doesn't exist yet: nothing ingested
        return StreamInfo(length=0, backlog=0, pending=0, dead_letters=0, consumers=[],
                          oldest_pending_ms=None)  # fmt: skip
    group = next((g for g in groups if _s(g["name"]) == GROUP), None)
    consumers: list[Consumer] = []
    oldest: int | None = None
    pending = 0
    backlog: int | None = length
    if group is not None:
        pending = int(group["pending"])
        lag = group.get("lag")
        backlog = None if lag is None else int(lag)  # None: Redis can't tell (after trims)
        consumers = [
            Consumer(name=_s(c["name"]), pending=int(c["pending"]), idle_ms=int(c["idle"]))
            for c in await r.xinfo_consumers(STREAM, GROUP)
        ]
        if pending:
            first = await r.xpending_range(STREAM, GROUP, min="-", max="+", count=1)
            if first:
                oldest = int(first[0]["time_since_delivered"])
    dead = await r.xlen(DEAD_LETTER_STREAM)
    return StreamInfo(length=length, backlog=backlog, pending=pending, dead_letters=dead,
                      consumers=consumers, oldest_pending_ms=oldest)  # fmt: skip


async def _rates(r: Redis, minutes: int) -> list[RatePoint]:
    now = time.time()
    stamps = [now - 60 * i for i in range(minutes - 1, -1, -1)]
    keys = [rate_key(kind, t) for t in stamps for kind in ("ingested", "written")]
    values = await r.mget(keys)
    points = []
    for i, t in enumerate(stamps):
        ingested, written = values[2 * i], values[2 * i + 1]
        points.append(
            RatePoint(
                minute=datetime.fromtimestamp(int(t // 60) * 60, UTC),
                ingested=int(ingested or 0),
                written=int(written or 0),
            )
        )
    return points


@router.get("/system", response_model=SystemStatus)
async def system_status(
    _project: Annotated[UUID, Depends(require_project)],
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    r: Annotated[Redis, Depends(get_redis)],
    minutes: Annotated[int, Query(ge=1, le=120)] = 30,
) -> SystemStatus:
    pg = await _postgres(pool)
    stream: StreamInfo | None = None
    rates: list[RatePoint] = []
    try:
        _, ms = await _timed(r.ping())
        rd = Dependency(ok=True, latency_ms=round(ms, 2))
        stream, _ = await _timed(_stream(r))
        rates, _ = await _timed(_rates(r, minutes))
    except Exception as exc:
        logger.warning("system: redis check failed: %r", exc)
        rd = Dependency(ok=False, latency_ms=None, error="unreachable")
    return SystemStatus(
        checked_at=datetime.now(UTC), postgres=pg, redis=rd, stream=stream, rates=rates
    )
