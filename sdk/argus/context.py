"""Which span is "current" right now, for the running thread or asyncio task.

A ContextVar is like a global variable, except that every thread and every asyncio task
sees its own value. asyncio.create_task() / gather() copy the current context when a
task is created, so a task starts with its parent's span as "current", and spans the
task opens never leak into sibling tasks. That is what keeps parents correct under
concurrency, with no locks and no explicit passing of span objects.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from argus.spans import Span

_current_span: ContextVar[Span | None] = ContextVar("argus_current_span", default=None)


def current_span() -> Span | None:
    """The innermost open span in this thread/task, or None outside any span."""
    return _current_span.get()


# Attributes copied into the metadata of every span CREATED while they are set (see
# spans.attributes()). Immutable mapping, so a nested `attributes()` builds a new one and
# leaving the block restores the outer one exactly, like the current span.
_EMPTY: Mapping[str, Any] = MappingProxyType({})
_attributes: ContextVar[Mapping[str, Any]] = ContextVar("argus_attributes", default=_EMPTY)


def current_attributes() -> Mapping[str, Any]:
    return _attributes.get()
