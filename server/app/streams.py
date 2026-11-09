"""Redis Stream names shared by the API (producer) and the worker (consumer)."""

import time

from fastapi import Request
from redis.asyncio import Redis

STREAM = "spans"  # one message per span: {"project_id": ..., "span": <SpanIn JSON>}
GROUP = "writers"  # consumer group: each message goes to exactly one worker
DEAD_LETTER_STREAM = "spans:dead"  # messages that can never be stored, kept for inspection

# Throughput counters for the System page: one Redis key per minute and direction,
# INCRBY'd by the API (spans accepted) and the worker (rows actually inserted).
RATE_TTL_S = 2 * 3600


def rate_key(kind: str, ts: float | None = None) -> str:
    """kind = "ingested" | "written"; the key holds that minute's span count."""
    return f"stats:{kind}:{int((time.time() if ts is None else ts) // 60)}"


def get_redis(request: Request) -> Redis:
    return request.app.state.redis
