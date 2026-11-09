"""Settings read from environment variables (set by docker-compose.yml or .env)."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    redis_url: str
    # How long a request waits for a free DB connection before we answer 503.
    db_pool_timeout_s: float
    # Approximate cap on the Redis stream length (XADD MAXLEN ~).
    stream_maxlen: int


def get_settings() -> Settings:
    # No defaults for URLs on purpose: a missing variable should fail loudly, and
    # connection strings with passwords must never live in code.
    return Settings(
        database_url=os.environ["DATABASE_URL"],
        redis_url=os.environ["REDIS_URL"],
        db_pool_timeout_s=float(os.environ.get("DB_POOL_TIMEOUT_S", "3")),
        stream_maxlen=int(os.environ.get("STREAM_MAXLEN", "1000000")),
    )
