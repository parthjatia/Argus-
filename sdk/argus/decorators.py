"""@observe: trace a whole function call (sync or async) as a span."""

import functools
import inspect
import logging
from collections.abc import Callable
from typing import Any, overload

from argus.spans import KINDS, Kind, span

logger = logging.getLogger("argus")


def _capture_args(fn: Callable[..., Any], args: tuple, kwargs: dict) -> Any:
    """Arguments as a {name: value} dict, without self/cls. Never raises."""
    try:
        bound = inspect.signature(fn).bind_partial(*args, **kwargs)
        return {k: v for k, v in bound.arguments.items() if k not in ("self", "cls")}
    except Exception:
        return {"args": list(args), "kwargs": kwargs}


@overload
def observe[F: Callable[..., Any]](func: F, /) -> F: ...
@overload
def observe[F: Callable[..., Any]](
    func: None = None,
    /,
    *,
    name: str | None = None,
    kind: Kind = "span",
    capture_input: bool = True,
    capture_output: bool = True,
) -> Callable[[F], F]: ...


def observe(
    func: Callable[..., Any] | None = None,
    /,
    *,
    name: str | None = None,
    kind: Kind = "span",
    capture_input: bool = True,
    capture_output: bool = True,
) -> Any:
    """Use as `@observe` or `@observe(name=..., kind=...)`.

    Exceptions mark the span as error and are re-raised unchanged.
    Generators are not supported: the span would end when the generator is created.
    """
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {sorted(KINDS)}, got {kind!r}")

    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        span_name = name or fn.__qualname__

        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                # A plain `with` is fine here: the ContextVar is set in this task's context.
                with span(span_name, kind) as s:
                    if capture_input:
                        s.set_input(_capture_args(fn, args, kwargs))
                    result = await fn(*args, **kwargs)
                    if capture_output:
                        s.set_output(result)
                    return result

            return async_wrapper

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with span(span_name, kind) as s:
                if capture_input:
                    s.set_input(_capture_args(fn, args, kwargs))
                result = fn(*args, **kwargs)
                if capture_output:
                    s.set_output(result)
                return result

        return wrapper

    return decorate(func) if func is not None else decorate
