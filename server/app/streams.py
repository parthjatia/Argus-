"""Redis Stream names shared by the API (producer) and the worker (consumer)."""

from fastapi import Request
from redis.asyncio import Redis

STREAM = "spans"  # one message per span: {"project_id": ..., "span": <SpanIn JSON>}
GROUP = "writers"  # consumer group: each message goes to exactly one worker
DEAD_LETTER_STREAM = "spans:dead"  # messages that can never be stored, kept for inspection


def get_redis(request: Request) -> Redis:
    return request.app.state.redis
