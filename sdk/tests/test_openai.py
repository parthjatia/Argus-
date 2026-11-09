import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

import argus
from argus import span

from .conftest import Collector


class Message:
    def __init__(self, content: str) -> None:
        self.content = content

    def model_dump(self, mode: str = "python") -> dict[str, Any]:  # like a Pydantic model
        return {"role": "assistant", "content": self.content}


def fake_response(content: str = "Cats are better.") -> SimpleNamespace:
    return SimpleNamespace(
        model="llama3.1:8b",
        choices=[SimpleNamespace(message=Message(content), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=42, completion_tokens=7),
    )


def make_client(create: Any) -> SimpleNamespace:
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


MESSAGES = [{"role": "user", "content": "Cats or dogs?"}]


def test_sync_client_records_a_generation(collector: Collector) -> None:
    client = argus.wrap_openai(make_client(lambda **kw: fake_response()))

    with span("turn"):
        resp = client.chat.completions.create(model="llama3.1", messages=MESSAGES, temperature=0)

    assert resp.choices[0].message.content == "Cats are better."  # response unchanged
    gen, turn = collector.by_name("openai.chat.completions"), collector.by_name("turn")
    assert gen["kind"] == "generation"
    assert gen["parent_span_id"] == turn["span_id"]
    assert gen["model"] == "llama3.1:8b"
    assert (gen["input_tokens"], gen["output_tokens"]) == (42, 7)
    assert gen["input"] == {"messages": MESSAGES, "params": {"temperature": 0}}
    assert gen["output"] == {"role": "assistant", "content": "Cats are better."}
    assert gen["metadata"] == {"finish_reason": "stop"}


def test_async_client_records_a_generation(collector: Collector) -> None:
    async def acreate(**kw: Any) -> SimpleNamespace:
        await asyncio.sleep(0)
        return fake_response("Dogs.")

    # Like the real SDK: a *sync* decorator wrapper that returns a coroutine.
    client = argus.wrap_openai(make_client(lambda **kw: acreate(**kw)))

    async def main() -> Any:
        with span("turn"):
            return await client.chat.completions.create(model="m", messages=MESSAGES)

    resp = asyncio.run(main())
    assert resp.choices[0].message.content == "Dogs."
    gen = collector.by_name("openai.chat.completions")
    assert gen["output"]["content"] == "Dogs."
    assert gen["parent_span_id"] == collector.by_name("turn")["span_id"]


def test_api_error_marks_generation_failed_and_reraises(collector: Collector) -> None:
    def create(**kw: Any) -> Any:
        raise TimeoutError("upstream timed out")

    client = argus.wrap_openai(make_client(create))
    with pytest.raises(TimeoutError):
        client.chat.completions.create(model="llama3.1", messages=MESSAGES)

    gen = collector.by_name("openai.chat.completions")
    assert gen["status"] == "error"
    assert gen["error_message"] == "TimeoutError: upstream timed out"
    assert gen["model"] == "llama3.1"  # from the request, since there's no response


def test_wrapping_twice_records_once(collector: Collector) -> None:
    client = make_client(lambda **kw: fake_response())
    argus.wrap_openai(argus.wrap_openai(client))
    client.chat.completions.create(model="m", messages=MESSAGES)
    assert len(collector.events) == 1
