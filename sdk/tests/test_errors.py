import asyncio

import pytest

import argus
from argus import observe, span

from .conftest import Collector


def test_exception_marks_span_error_and_is_reraised(collector: Collector) -> None:
    @observe
    def boom() -> None:
        raise ValueError("bad input")

    with pytest.raises(ValueError, match="bad input"):
        boom()

    e = collector.by_name("test_exception_marks_span_error_and_is_reraised.<locals>.boom")
    assert e["status"] == "error"
    assert e["error_message"] == "ValueError: bad input"
    assert argus.current_span() is None  # context restored after the exception


def test_async_exception_marks_span_error_and_is_reraised(collector: Collector) -> None:
    @observe(name="aboom")
    async def aboom() -> None:
        await asyncio.sleep(0)
        raise KeyError("missing")

    with pytest.raises(KeyError):
        asyncio.run(aboom())
    assert collector.by_name("aboom")["status"] == "error"


def test_exception_caught_by_parent_only_marks_the_child(collector: Collector) -> None:
    with span("outer"):
        try:
            with span("inner"):
                raise RuntimeError("timeout")
        except RuntimeError:
            pass  # e.g. a retry: outer still succeeds
        with span("after"):
            pass

    assert collector.by_name("inner")["status"] == "error"
    assert collector.by_name("outer")["status"] == "ok"
    # `after` must be a child of outer (context was reset when inner failed).
    assert collector.by_name("after")["parent_span_id"] == collector.by_name("outer")["span_id"]


def test_sdk_failure_never_breaks_user_code(collector: Collector, monkeypatch) -> None:
    def broken_enqueue(event, size=0):
        raise RuntimeError("exporter exploded")

    monkeypatch.setattr(collector, "enqueue", broken_enqueue)
    with span("x"):
        result = 1 + 1
    assert result == 2  # no exception escaped
