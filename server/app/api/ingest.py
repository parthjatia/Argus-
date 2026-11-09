"""POST /v1/ingest.

Checks run cheapest-first and stop at the first failure:
  1. body size  → 413      2. API key → 401      3. validation → 422
  4. XADD to the Redis stream → 202 (or 503 if Postgres/Redis is down, see main.py)

A worker (worker/consumer.py) writes the spans to Postgres afterwards.
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from redis.asyncio import Redis

from app.auth import require_project
from app.config import get_settings
from app.schemas import IngestBatch, IngestResponse
from app.streams import RATE_TTL_S, STREAM, get_redis, rate_key

logger = logging.getLogger("argus.ingest")

MAX_BODY_BYTES = 5 * 1024 * 1024

router = APIRouter()


def _too_large() -> HTTPException:
    return HTTPException(status_code=413, detail=f"request body over {MAX_BODY_BYTES} bytes")


def check_content_length(content_length: Annotated[int | None, Header()] = None) -> None:
    # Fast path: reject before reading anything, based on the declared size.
    if content_length is not None and content_length > MAX_BODY_BYTES:
        raise _too_large()


async def read_body_limited(request: Request) -> bytes:
    # The header can be missing (chunked upload) or wrong, so also count while reading.
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise _too_large()
        chunks.append(chunk)
    return b"".join(chunks)


@router.post(
    "/v1/ingest",
    status_code=202,
    response_model=IngestResponse,
    # Route-level dependencies run before parameter dependencies → size check first.
    dependencies=[Depends(check_content_length)],
)
async def ingest(
    request: Request,
    project_id: Annotated[UUID, Depends(require_project)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> IngestResponse:
    # We parse the body ourselves instead of declaring `batch: IngestBatch`, because
    # FastAPI would parse it BEFORE running the auth dependency. This way
    # unauthenticated callers get 401 without learning anything about the format.
    body = await read_body_limited(request)
    try:
        batch = IngestBatch.model_validate_json(body)
    except ValidationError as exc:
        # Same 422 shape FastAPI uses; drop "input" so we don't echo huge payloads back.
        errors = [
            {k: v for k, v in err.items() if k != "input"} | {"loc": ("body", *err["loc"])}
            for err in exc.errors(include_url=False)
        ]
        raise RequestValidationError(errors) from exc

    # One stream message per span. MULTI/EXEC (transaction=True) makes the batch
    # all-or-nothing in Redis too; if it fails the client gets 503 and retries, and the
    # worker's ON CONFLICT DO NOTHING absorbs anything that was written twice.
    # MAXLEN ~ caps memory: Redis trims the oldest entries (approximately, cheaply).
    maxlen = get_settings().stream_maxlen
    async with redis.pipeline(transaction=True) as pipe:
        for span in batch.batch:
            pipe.xadd(
                STREAM,
                {"project_id": str(project_id), "span": span.model_dump_json()},
                maxlen=maxlen,
                approximate=True,
            )
        counter = rate_key("ingested")
        pipe.incrby(counter, len(batch.batch))
        pipe.expire(counter, RATE_TTL_S)
        await pipe.execute()

    logger.debug("ingest project=%s queued=%d", project_id, len(batch.batch))
    return IngestResponse(accepted=len(batch.batch))
