"""Span objects and the `span()` context manager."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from time import perf_counter
from types import TracebackType
from typing import Any, Literal
from uuid import uuid4

from argus import client
from argus.context import _current_span, current_span
from argus.models import SpanEvent
from argus.serialize import to_jsonable

logger = logging.getLogger("argus")

Kind = Literal["span", "generation", "tool"]
KINDS: frozenset[str] = frozenset({"span", "generation", "tool"})

# Mirror the server's limits so one bad span never gets a whole batch rejected (422).
MAX_NAME_CHARS = 200
MAX_MODEL_CHARS = 200
MAX_ERROR_CHARS = 10_000


class Span:
    """One unit of work. Created by `span()` / `@observe`; sent to the server when it ends."""

    def __init__(
        self,
        name: str,
        kind: str = "span",
        *,
        parent: Span | None = None,
        model: str | None = None,
    ) -> None:
        if kind not in KINDS:
            logger.warning("argus: unknown span kind %r, using 'span'", kind)
            kind = "span"
        self.span_id = str(uuid4())
        # A child joins its parent's trace; a root span starts a new trace.
        self.trace_id = parent.trace_id if parent else str(uuid4())
        self.parent_span_id = parent.span_id if parent else None
        self.name = (name or "unnamed")[:MAX_NAME_CHARS]
        self.kind = kind
        self.model = model
        self.start_time = datetime.now(UTC)
        self._t0 = perf_counter()
        self.status = "ok"
        self.error_message: str | None = None
        self.input: Any = None
        self.output: Any = None
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None
        self.metadata: dict[str, Any] = {}
        self._size = 0
        self._input_size = 0
        self._output_size = 0
        self._ended = False

    # --- setters users call inside a span ------------------------------------------------

    def set_input(self, value: Any) -> None:
        self.input, self._input_size = to_jsonable(value, client.max_payload_bytes())

    def set_output(self, value: Any) -> None:
        self.output, self._output_size = to_jsonable(value, client.max_payload_bytes())

    def set_model(self, model: str | None) -> None:
        self.model = model

    def set_usage(self, input_tokens: int | None = None, output_tokens: int | None = None) -> None:
        self.input_tokens = _non_negative_int(input_tokens)
        self.output_tokens = _non_negative_int(output_tokens)

    def set_metadata(self, **values: Any) -> None:
        safe, _ = to_jsonable(values, client.max_payload_bytes())
        if isinstance(safe, dict):
            self.metadata.update(safe)

    # --- ending ---------------------------------------------------------------------------

    def end(self, error: BaseException | None = None) -> None:
        """Finish the span and hand it to the exporter. Safe to call twice; never raises."""
        if self._ended:
            return
        self._ended = True
        try:
            # Duration from a monotonic clock: if the wall clock jumps backwards (NTP),
            # end_time still never comes before start_time (the server would reject it).
            end_time = self.start_time + timedelta(seconds=perf_counter() - self._t0)
            if error is not None:
                self.status = "error"
                self.error_message = f"{type(error).__name__}: {error}"[:MAX_ERROR_CHARS]
            model = self.model[:MAX_MODEL_CHARS] if self.model else None
            if self.kind == "generation" and not model:
                model = "unknown"  # server requires a model for generations
            event = SpanEvent(
                span_id=self.span_id,
                trace_id=self.trace_id,
                parent_span_id=self.parent_span_id,
                name=self.name,
                kind=self.kind,
                start_time=self.start_time.isoformat(),
                end_time=end_time.isoformat(),
                status=self.status,
                error_message=self.error_message,
                model=model,
                input=self.input,
                output=self.output,
                input_tokens=self.input_tokens,
                output_tokens=self.output_tokens,
                metadata=self.metadata or None,
            )
            size = 600 + len(self.name) + self._input_size + self._output_size
            client.emit(event.to_dict(), size)
        except Exception:
            logger.exception("argus: failed to record span %r", self.name)


def _non_negative_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


class _SpanContext:
    """Returned by `span()`. Works with both `with` and `async with`."""

    def __init__(self, name: str, kind: str, input: Any, metadata: dict[str, Any] | None,
                 model: str | None) -> None:  # fmt: skip
        self._args = (name, kind, input, metadata, model)
        self._span: Span | None = None

    def __enter__(self) -> Span:
        name, kind, input, metadata, model = self._args
        parent = current_span()
        s = Span(name, kind, parent=parent, model=model)
        if input is not None:
            s.set_input(input)
        if metadata:
            s.set_metadata(**metadata)
        self._span, self._parent = s, parent
        # Make this span "current" so anything opened inside becomes its child.
        self._token = _current_span.set(s)
        return s

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:
        # Restore the previous span FIRST, so the context is right even if ending fails.
        try:
            _current_span.reset(self._token)
        except ValueError:  # exited in a different context than entered; fall back
            _current_span.set(self._parent)
        assert self._span is not None
        self._span.end(exc)
        return False  # never swallow the user's exception

    async def __aenter__(self) -> Span:
        return self.__enter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:
        return self.__exit__(exc_type, exc, tb)


def span(
    name: str,
    kind: Kind = "span",
    *,
    input: Any = None,
    metadata: dict[str, Any] | None = None,
    model: str | None = None,
) -> _SpanContext:
    """Trace a block of code: `with argus.span("retrieve", kind="tool") as s: ...`."""
    return _SpanContext(name, kind, input, metadata, model)
