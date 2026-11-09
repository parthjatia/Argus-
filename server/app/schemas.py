"""Pydantic models for the HTTP boundary."""

from typing import Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    NonNegativeInt,
    model_validator,
)

MAX_SPANS_PER_BATCH = 1000


class SpanIn(BaseModel):
    """One finished span, as sent by the SDK (see "Event format" in CLAUDE.md)."""

    # Unknown fields are ignored, so a newer SDK can talk to an older server.
    # (This also means a `project_id` in the body is silently dropped.)
    model_config = ConfigDict(extra="ignore")

    span_id: UUID
    trace_id: UUID
    parent_span_id: UUID | None = None
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["span", "generation", "tool"]
    start_time: AwareDatetime  # rejects timestamps without a timezone
    end_time: AwareDatetime
    status: Literal["ok", "error"]
    error_message: str | None = Field(default=None, max_length=10_000)
    model: str | None = Field(default=None, max_length=200)
    input: JsonValue = None
    output: JsonValue = None
    input_tokens: NonNegativeInt | None = None
    output_tokens: NonNegativeInt | None = None
    metadata: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def _check_cross_field_rules(self) -> Self:
        if self.end_time < self.start_time:
            raise ValueError("end_time must not be before start_time")
        if self.kind == "generation" and not self.model:
            raise ValueError("model is required when kind is 'generation'")
        return self


class IngestBatch(BaseModel):
    batch: list[SpanIn] = Field(min_length=1, max_length=MAX_SPANS_PER_BATCH)


class IngestResponse(BaseModel):
    # Only "accepted": once ingest goes through Redis (Milestone 3), the server can't
    # know at request time how many spans were new, so we never promise that number.
    accepted: int
