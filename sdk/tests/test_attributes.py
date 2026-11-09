import asyncio

import argus
from argus import span

from .conftest import Collector


def meta(collector: Collector, name: str) -> dict:
    return collector.by_name(name)["metadata"] or {}


def test_attributes_reach_every_span_created_inside(collector: Collector) -> None:
    with argus.attributes(topic="t", rounds=3):
        with span("root"):
            with argus.attributes(round=1), span("round"):
                with argus.attributes(role="pro"), span("turn"):
                    pass
    with span("outside"):
        pass

    assert meta(collector, "root") == {"topic": "t", "rounds": 3}
    assert meta(collector, "round") == {"topic": "t", "rounds": 3, "round": 1}
    assert meta(collector, "turn") == {"topic": "t", "rounds": 3, "round": 1, "role": "pro"}
    assert meta(collector, "outside") == {}  # restored after the block


def test_inner_attributes_and_own_metadata_win(collector: Collector) -> None:
    with argus.attributes(role="pro", round=1):
        with argus.attributes(role="con"):
            with span("a", metadata={"round": 9}):
                pass
        with span("b"):
            pass
    assert meta(collector, "a") == {"role": "con", "round": 9}
    assert meta(collector, "b") == {"role": "pro", "round": 1}


def test_attributes_dont_change_spans_that_already_exist(collector: Collector) -> None:
    with span("early"):
        with argus.attributes(role="judge"), span("late"):
            pass
    assert meta(collector, "early") == {}
    assert meta(collector, "late") == {"role": "judge"}


def test_attributes_follow_asyncio_tasks_without_leaking(collector: Collector) -> None:
    async def turn(side: str) -> None:
        with argus.attributes(role=side):
            await asyncio.sleep(0.01)
            with span(f"turn-{side}"):
                await asyncio.sleep(0.01)

    async def main() -> None:
        with argus.attributes(topic="t"):
            await asyncio.gather(turn("pro"), turn("con"))

    asyncio.run(main())
    assert meta(collector, "turn-pro") == {"topic": "t", "role": "pro"}
    assert meta(collector, "turn-con") == {"topic": "t", "role": "con"}


def test_attributes_reach_wrap_openai_generations(collector: Collector) -> None:
    from types import SimpleNamespace

    class Completions:
        def create(self, **kwargs: object) -> object:
            return SimpleNamespace(model="m", choices=[], usage=None)

    llm = argus.wrap_openai(SimpleNamespace(chat=SimpleNamespace(completions=Completions())))
    with argus.attributes(role="pro"):
        llm.chat.completions.create(model="m", messages=[])
    assert meta(collector, "openai.chat.completions")["role"] == "pro"
