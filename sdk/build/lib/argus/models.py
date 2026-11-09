"""Wire format sent to POST /v1/ingest (see "Event format" in CLAUDE.md)."""

from dataclasses import dataclass, fields
from typing import Any


@dataclass(slots=True)
class SpanEvent:
    span_id: str
    trace_id: str
    parent_span_id: str | None
    name: str
    kind: str
    start_time: str  # ISO 8601 with timezone
    end_time: str
    status: str
    error_message: str | None
    model: str | None
    input: Any
    output: Any
    input_tokens: int | None
    output_tokens: int | None
    metadata: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}
