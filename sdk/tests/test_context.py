import asyncio
from datetime import datetime

import argus
from argus import observe, span

from .conftest import Collector


def test_nested_sync_spans_get_the_correct_parent(collector: Collector) -> None:
    with span("root") as root:
        with span("child-a"):
            with span("grandchild"):
                pass
        with span("child-b"):  # sibling: parent must be root again, not child-a
            pass

    r, a, g, b = (collector.by_name(n) for n in ("root", "child-a", "grandchild", "child-b"))
    assert r["parent_span_id"] is None
    assert a["parent_span_id"] == r["span_id"]
    assert g["parent_span_id"] == a["span_id"]
    assert b["parent_span_id"] == r["span_id"]
    assert {e["trace_id"] for e in collector.events} == {root.trace_id}
    # Spans are sent when they END: innermost first, root last.
    assert [e["name"] for e in collector.events] == ["grandchild", "child-a", "child-b", "root"]


def test_no_current_span_outside_and_after(collector: Collector) -> None:
    assert argus.current_span() is None
    with span("x") as s:
        assert argus.current_span() is s
    assert argus.current_span() is None


def test_separate_roots_are_separate_traces(collector: Collector) -> None:
    with span("one"):
        pass
    with span("two"):
        pass
    assert collector.by_name("one")["trace_id"] != collector.by_name("two")["trace_id"]


def test_nested_async_and_concurrent_tasks_dont_mix_parents(collector: Collector) -> None:
    @observe(name="worker")
    async def worker(i: int) -> int:
        # Uneven sleeps force the tasks to interleave.
        await asyncio.sleep(0.001 * (10 - i))
        async with span(f"child-{i}"):
            await asyncio.sleep(0.001 * i)
        return i

    @observe(name="main")
    async def main() -> list[int]:
        return await asyncio.gather(*(worker(i) for i in range(10)))

    asyncio.run(main())

    main_ev = collector.by_name("main")
    workers = {e["input"]["i"]: e for e in collector.events if e["name"] == "worker"}
    assert len(workers) == 10
    for i, w in workers.items():
        assert w["parent_span_id"] == main_ev["span_id"]
        assert collector.by_name(f"child-{i}")["parent_span_id"] == w["span_id"]
    assert {e["trace_id"] for e in collector.events} == {main_ev["trace_id"]}


def test_concurrent_root_tasks_get_their_own_traces(collector: Collector) -> None:
    async def job(i: int) -> None:
        with span(f"root-{i}"):
            await asyncio.sleep(0.001)
            with span(f"leaf-{i}"):
                await asyncio.sleep(0.001)

    async def main() -> None:
        await asyncio.gather(job(0), job(1))

    asyncio.run(main())
    for i in (0, 1):
        root, leaf = collector.by_name(f"root-{i}"), collector.by_name(f"leaf-{i}")
        assert root["parent_span_id"] is None
        assert leaf["parent_span_id"] == root["span_id"]
        assert leaf["trace_id"] == root["trace_id"]
    assert collector.by_name("root-0")["trace_id"] != collector.by_name("root-1")["trace_id"]


def test_times_are_timezone_aware_and_ordered(collector: Collector) -> None:
    with span("t"):
        pass
    e = collector.by_name("t")
    start, end = datetime.fromisoformat(e["start_time"]), datetime.fromisoformat(e["end_time"])
    assert start.tzinfo is not None
    assert end >= start
