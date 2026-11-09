"""Settings read from environment variables (set by docker-compose.yml or .env)."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    redis_url: str


def get_settings() -> Settings:
    # No defaults on purpose: a missing variable should fail loudly, and
    # connection strings with passwords must never live in code.
    return Settings(
        database_url=os.environ["DATABASE_URL"],
        redis_url=os.environ["REDIS_URL"],
    )
