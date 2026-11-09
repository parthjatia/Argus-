import json
import threading
import time
from collections.abc import Callable

import httpx
import pytest

from argus.exporter import Exporter


class FakeServer:
    """httpx.MockTransport handler that records requests and replays scripted responses."""

    def __init__(self, responses: list[int | Exception] | None = None, delay: float = 0) -> None:
        self.responses = list(responses or [])
        self.delay = delay
        self.batches: list[list[dict]] = []
        self.headers: list[httpx.Headers] = []
        self.lock = threading.Lock()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.delay:
            time.sleep(self.delay)
        with self.lock:
            self.headers.append(request.headers)
            outcome = self.responses.pop(0) if self.responses else 202
            if isinstance(outcome, Exception):
                raise outcome
            if outcome < 300:
                self.batches.append(json.loads(request.content)["batch"])
        return httpx.Response(outcome, json={})

    @property
    def attempts(self) -> int:
        return len(self.headers)


@pytest.fixture
def make_exporter() -> Callable[..., Exporter]:
    created: list[Exporter] = []

    def make(server: FakeServer, **kwargs) -> Exporter:
        opts = {"flush_interval": 0.05, "backoff_base": 0.001, "backoff_max": 0.01} | kwargs
        exp = Exporter("argus_test", "http://argus.test", transport=httpx.MockTransport(server),
                       **opts)  # fmt: skip
        created.append(exp)
        return exp

    yield make
    for exp in created:
        exp.shutdown(timeout=0.5)


def ev(i: int) -> dict:
    return {"span_id": str(i)}


def test_batches_by_count(make_exporter) -> None:
    server = FakeServer()
    exp = make_exporter(server, batch_size=100)
    for i in range(250):
        exp.enqueue(ev(i))
    assert exp.flush(timeout=2)
    assert sorted(len(b) for b in server.batches) == [50, 100, 100]
    assert [e["span_id"] for b in server.batches for e in b] == [str(i) for i in range(250)]
    assert server.headers[0]["x-api-key"] == "argus_test"
    assert exp.stats()["sent"] == 250


def test_partial_batch_is_sent_after_the_interval(make_exporter) -> None:
    server = FakeServer()
    make_exporter(server, flush_interval=0.05).enqueue(ev(1))
    deadline = time.monotonic() + 2
    while not server.batches and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.batches == [[ev(1)]]  # no flush() call needed


def test_batches_by_bytes(make_exporter) -> None:
    server = FakeServer()
    exp = make_exporter(server, max_batch_bytes=1000)
    for i in range(4):
        exp.enqueue(ev(i), size=400)  # two fit per request
    assert exp.flush(timeout=2)
    assert [len(b) for b in server.batches] == [2, 2]


def test_never_blocks_the_caller_when_server_is_down(make_exporter) -> None:
    # Every request hangs 200 ms and then fails, like a dead server behind a timeout.
    server = FakeServer([httpx.ConnectError("down")] * 1000, delay=0.2)
    exp = make_exporter(server, max_queue_size=1000)

    start = time.perf_counter()
    for i in range(20_000):
        exp.enqueue(ev(i))
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0  # ~µs per call; a single blocking send would take 200 ms
    assert exp.stats()["dropped"] >= 18_000  # bounded queue, excess dropped and counted


def test_queue_overflow_drops_oldest_and_counts(make_exporter) -> None:
    exp = make_exporter(FakeServer(), max_queue_size=3, start=False)
    for i in range(5):
        exp.enqueue(ev(i))
    assert [e["span_id"] for e, _ in exp._queue] == ["2", "3", "4"]  # 0 and 1 dropped
    assert exp.stats()["dropped"] == 2


def test_retries_5xx_and_network_errors_then_succeeds(make_exporter) -> None:
    server = FakeServer([503, httpx.ConnectError("refused"), 500, 202])
    exp = make_exporter(server)
    exp.enqueue(ev(1))
    assert exp.flush(timeout=2)
    assert server.attempts == 4
    assert server.batches == [[ev(1)]]
    assert exp.stats()["failed"] == 0


def test_no_retry_on_4xx(make_exporter) -> None:
    server = FakeServer([422])
    exp = make_exporter(server)
    exp.enqueue(ev(1))
    assert exp.flush(timeout=2)
    assert server.attempts == 1
    assert exp.stats()["failed"] == 1


def test_gives_up_after_max_retries(make_exporter) -> None:
    server = FakeServer([503] * 10)
    exp = make_exporter(server, max_retries=3)
    exp.enqueue(ev(1))
    assert exp.flush(timeout=2)
    assert server.attempts == 4  # first try + 3 retries
    assert exp.stats()["failed"] == 1


def test_backoff_grows_exponentially_with_jitter(monkeypatch) -> None:
    exp = Exporter("k", "http://x", backoff_base=0.5, backoff_max=10, start=False)
    caps = []
    monkeypatch.setattr("argus.exporter.random.uniform", lambda lo, hi: caps.append(hi) or 0)
    for attempt in range(6):
        exp._backoff_sleep(attempt)
    assert caps == [0.5, 1, 2, 4, 8, 10]  # doubles, capped at backoff_max
    exp.shutdown(0)


def test_shutdown_flushes_pending_events(make_exporter) -> None:
    server = FakeServer()
    exp = make_exporter(server, flush_interval=60)  # interval alone would never fire
    for i in range(5):
        exp.enqueue(ev(i))
    exp.shutdown(timeout=2)
    assert sum(len(b) for b in server.batches) == 5


def test_shutdown_respects_its_time_limit(make_exporter) -> None:
    server = FakeServer(delay=2)  # every request hangs for 2 s
    exp = make_exporter(server)
    exp.enqueue(ev(1))
    time.sleep(0.1)  # let the sender start the hanging request
    start = time.perf_counter()
    exp.shutdown(timeout=0.3)
    assert time.perf_counter() - start < 1.0


def test_enqueue_after_shutdown_is_dropped_quietly(make_exporter) -> None:
    exp = make_exporter(FakeServer())
    exp.shutdown(timeout=0.5)
    exp.enqueue(ev(1))  # must not raise
    assert exp.stats()["dropped"] == 1
