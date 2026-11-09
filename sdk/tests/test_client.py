import json

import httpx
import pytest

import argus
from argus import client


@pytest.fixture(autouse=True)
def _reset() -> None:
    yield
    argus.shutdown(timeout=0.5)


def test_without_api_key_sdk_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARGUS_API_KEY", raising=False)
    argus.init()
    with argus.span("x"):
        pass  # works, just nothing exported
    assert client._exporter is None
    assert argus.flush() is True


def test_init_sends_spans_through_the_exporter(monkeypatch: pytest.MonkeyPatch) -> None:
    received: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        received.extend(json.loads(request.content)["batch"])
        return httpx.Response(202, json={"accepted": 1})

    monkeypatch.setenv("ARGUS_API_KEY", "argus_from_env")
    argus.init(transport=httpx.MockTransport(handler))

    @argus.observe(name="root")
    def root() -> None:
        with argus.span("child"):
            pass

    root()
    assert argus.flush(timeout=2)
    assert [e["name"] for e in received] == ["child", "root"]
    assert argus.get_stats()["sent"] == 2
