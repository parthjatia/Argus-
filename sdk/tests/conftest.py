from collections.abc import Iterator
from typing import Any

import pytest

from argus import client


class Collector:
    """Stands in for the Exporter: records events instead of sending them."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def enqueue(self, event: dict[str, Any], size: int = 0) -> None:
        self.events.append(event)

    def by_name(self, name: str) -> dict[str, Any]:
        matches = [e for e in self.events if e["name"] == name]
        assert len(matches) == 1, f"expected one span named {name!r}, got {len(matches)}"
        return matches[0]


@pytest.fixture
def collector(monkeypatch: pytest.MonkeyPatch) -> Iterator[Collector]:
    c = Collector()
    monkeypatch.setattr(client, "_exporter", c)
    yield c
