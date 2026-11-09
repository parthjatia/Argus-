import pytest

from argus import observe, span
from argus.serialize import TRUNCATED_KEY

from .conftest import Collector


class Agent:
    @observe(name="agent.reply", kind="generation")
    def reply(self, prompt: str, temperature: float = 0.5) -> str:
        return prompt.upper()


def test_observe_captures_args_and_result(collector: Collector) -> None:
    assert Agent().reply("hi", temperature=0.1) == "HI"
    e = collector.by_name("agent.reply")
    assert e["kind"] == "generation"
    assert e["input"] == {"prompt": "hi", "temperature": 0.1}  # no `self`
    assert e["output"] == "HI"
    assert e["model"] == "unknown"  # generations always get a model (server requires one)


def test_bare_decorator_uses_qualname(collector: Collector) -> None:
    @observe
    def add(a: int, b: int) -> int:
        return a + b

    assert add(2, 3) == 5
    assert collector.events[0]["name"].endswith("add")
    assert collector.events[0]["input"] == {"a": 2, "b": 3}


def test_capture_can_be_disabled(collector: Collector) -> None:
    @observe(name="secret", capture_input=False, capture_output=False)
    def f(password: str) -> str:
        return password

    f("hunter2")
    e = collector.by_name("secret")
    assert e["input"] is None and e["output"] is None


def test_invalid_kind_fails_at_decoration_time() -> None:
    with pytest.raises(ValueError):
        observe(kind="banana")  # type: ignore[arg-type]


def test_large_input_is_truncated(collector: Collector) -> None:
    with span("big", input="x" * 100_000):
        pass
    value = collector.by_name("big")["input"]
    assert value[TRUNCATED_KEY] is True
    assert value["original_bytes"] > 100_000
    assert len(value["preview"].encode()) <= 32 * 1024


def test_non_json_values_are_made_safe(collector: Collector) -> None:
    class Thing:
        def __repr__(self) -> str:
            return "<Thing>"

    with span("odd", input={"thing": Thing(), "nan": float("nan")}):
        pass
    assert isinstance(collector.by_name("odd")["input"], str)  # fell back to repr


def test_span_setters(collector: Collector) -> None:
    with span("gen", kind="generation", model="llama3.1", metadata={"round": 1}) as s:
        s.set_usage(input_tokens=10, output_tokens=-5)  # negative is dropped
        s.set_output({"text": "ok"})
        s.set_metadata(judge=True)
    e = collector.by_name("gen")
    assert e["model"] == "llama3.1"
    assert (e["input_tokens"], e["output_tokens"]) == (10, None)
    assert e["metadata"] == {"round": 1, "judge": True}
    assert e["output"] == {"text": "ok"}


def test_long_names_are_cut_to_server_limit(collector: Collector) -> None:
    with span("n" * 500):
        pass
    assert len(collector.events[0]["name"]) == 200
