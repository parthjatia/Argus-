"""API keys: generation, hashing, and the FastAPI dependency that maps a key to a project."""

import hashlib
import secrets
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException
from psycopg_pool import AsyncConnectionPool

from app.db import get_pool

API_KEY_PREFIX = "argus_"


def generate_api_key() -> str:
    # 32 random bytes = 256 bits of entropy. The prefix makes keys recognisable
    # in logs and by secret scanners (e.g. GitHub push protection).
    return API_KEY_PREFIX + secrets.token_urlsafe(32)


def hash_api_key(raw_key: str) -> str:
    # Plain SHA-256 is enough here (no bcrypt/argon2): those slow hashes protect
    # low-entropy human passwords from guessing; a 256-bit random key can't be
    # guessed. A fast, deterministic hash also lets us look the key up by its
    # hash with the UNIQUE index on api_keys.key_hash.
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "ApiKey"})


async def require_project(
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    x_api_key: Annotated[str | None, Header()] = None,
) -> UUID:
    """Return the project_id for the request's X-API-Key, or raise 401.

    The project comes ONLY from the key, never from the request body.
    """
    if not x_api_key:
        raise _unauthorized("missing API key")

    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT project_id FROM api_keys WHERE key_hash = %s AND revoked_at IS NULL",
            (hash_api_key(x_api_key),),
        )
        row = await cur.fetchone()

    if row is None:
        # Same message for unknown and revoked keys: don't reveal which keys exist.
        raise _unauthorized("invalid API key")
    return row[0]
