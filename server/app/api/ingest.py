"""POST /v1/ingest.

Checks run cheapest-first and stop at the first failure:
  1. body size  → 413      2. API key → 401      3. validation → 422
  4. DB insert  → 202 (or 503 if Postgres is unreachable, see main.py)
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from psycopg_pool import AsyncConnectionPool
from pydantic import ValidationError

from app.auth import require_project
from app.db import get_pool
from app.schemas import IngestBatch, IngestResponse
from app.storage import store_spans

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
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
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

    async with pool.connection() as conn:
        inserted = await store_spans(conn, project_id, batch.batch)

    logger.info("ingest project=%s accepted=%d inserted=%d", project_id, len(batch.batch), inserted)
    return IngestResponse(accepted=len(batch.batch))
