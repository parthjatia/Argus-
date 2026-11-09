"""Test-only stand-in for an OpenAI-compatible LLM. Lives in tests/ so the app can't use it."""

from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from arena.faults import Faults
from arena.llm import ROLES, Role, RoleConfig, wrap
from argus import client as argus_client

MODELS: dict[Role, str] = {"pro": "test-pro", "con": "test-con", "judge": "test-judge"}
VERDICT = '{"winner": "pro", "scores": {"pro": 7, "con": 5}, "reasoning": "More evidence."}'


class StubCompletions:
    """`.create()` returns an OpenAI-shaped response. `script` holds exceptions to raise on
    the first calls (None = answer normally); after that every call answers normally."""

    def __init__(self, script: list[Exception | None] | None = None) -> None:
        self.script = list(script or [])
        self.calls = 0

    def create(self, *, model: str, messages: list[dict[str, str]], **kwargs: Any) -> Any:
        self.calls += 1
        if self.script and (exc := self.script.pop(0)) is not None:
            raise exc
        json_mode = (kwargs.get("response_format") or {}).get("type") == "json_object"
        content = VERDICT if json_mode else f"Argument {self.calls}: the evidence favours us."
        prompt = sum(len(m["content"]) for m in messages)
        return SimpleNamespace(
            model=model,
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(role="assistant", content=content), finish_reason="stop"
                )
            ],  # fmt: skip
            usage=SimpleNamespace(
                prompt_tokens=max(1, prompt // 4), completion_tokens=max(1, len(content) // 4)
            ),  # fmt: skip
        )


Clients = dict[Role, tuple[RoleConfig, Any]]


@pytest.fixture
def make_stub_clients() -> Callable[..., Clients]:
    """Traced stub clients per role. `faults` is shared; `pro=[...]` etc. script errors."""

    def make(faults: Faults | None = None, **scripts: list[Exception | None]) -> Clients:
        faults = faults or Faults()
        out: Clients = {}
        for role in ROLES:
            cfg = RoleConfig(role, "http://llm.test/v1", MODELS[role], "test-key", 1.0)
            out[role] = (cfg, wrap(StubCompletions(scripts.get(role)), faults))
        return out

    return make


class Collector:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def enqueue(self, event: dict[str, Any], size: int = 0) -> None:
        self.events.append(event)

    def named(self, name: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["name"] == name]

    def one(self, name: str) -> dict[str, Any]:
        (e,) = self.named(name)
        return e

    def by_id(self) -> dict[str, dict[str, Any]]:
        return {e["span_id"]: e for e in self.events}


@pytest.fixture
def spans(monkeypatch: pytest.MonkeyPatch) -> Iterator[Collector]:
    """Captures exported spans in memory instead of sending them."""
    c = Collector()
    monkeypatch.setattr(argus_client, "_exporter", c)
    yield c


@pytest.fixture
def waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Records retry waits instead of sleeping."""
    recorded: list[float] = []
    monkeypatch.setattr("arena.llm._sleep", recorded.append)
    return recorded
