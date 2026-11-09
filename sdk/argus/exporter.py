"""Background exporter: a bounded in-memory queue drained by one daemon thread.

The host app only ever calls `enqueue()`, which takes a lock for a few microseconds
and never does I/O. All network work (batching, HTTP, retries, sleeping) happens on
the exporter thread, so a slow or dead server can't slow down or crash the app.
"""

import logging
import random
import threading
from collections import deque
from time import monotonic
from typing import Any

import httpx

logger = logging.getLogger("argus")

Event = dict[str, Any]


class Exporter:
    def __init__(
        self,
        api_key: str,
        host: str,
        *,
        max_queue_size: int = 10_000,
        batch_size: int = 100,
        max_batch_bytes: int = 4_000_000,  # server rejects bodies over 5 MB
        flush_interval: float = 1.0,
        max_retries: int = 5,
        backoff_base: float = 0.5,
        backoff_max: float = 10.0,
        timeout: float = 5.0,
        transport: httpx.BaseTransport | None = None,  # tests inject httpx.MockTransport
        start: bool = True,
    ) -> None:
        self._max_queue_size = max_queue_size
        self._batch_size = batch_size
        self._max_batch_bytes = max_batch_bytes
        self._flush_interval = flush_interval
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max

        self._queue: deque[tuple[Event, int]] = deque()
        # One Condition (a lock + wait/notify) guards all mutable state below.
        self._cond = threading.Condition()
        self._busy = False  # a batch is being sent right now
        self._flush_requested = False
        self._stopping = False
        self._deadline: float | None = None  # set by shutdown(): give up after this
        self._stop_event = threading.Event()  # interrupts backoff sleeps on shutdown

        self.dropped = 0  # events lost because the queue was full (or after shutdown)
        self.sent = 0
        self.failed = 0  # events given up on (4xx, retries exhausted, shutdown timeout)

        self._client = httpx.Client(
            base_url=host,
            headers={"X-API-Key": api_key, "User-Agent": "argus-python/0.1.0"},
            # Short timeouts: a hanging server must not hold the queue forever.
            timeout=httpx.Timeout(timeout, connect=2.0),
            transport=transport,
        )
        # daemon=True: this thread never keeps the interpreter alive on exit.
        self._thread = threading.Thread(target=self._run, name="argus-exporter", daemon=True)
        if start:
            self._thread.start()

    # --- called from the host app (must be fast and never raise) --------------------------

    def enqueue(self, event: Event, size: int = 0) -> None:
        dropped_now = 0
        with self._cond:
            if self._stopping:
                self.dropped += 1
                return
            if len(self._queue) >= self._max_queue_size:
                # Drop the OLDEST event: never block the caller, never grow without bound.
                self._queue.popleft()
                self.dropped += 1
                dropped_now = self.dropped
            self._queue.append((event, size))
            if len(self._queue) >= self._batch_size:
                self._cond.notify_all()  # a full batch is ready: wake the sender early
        if dropped_now and (dropped_now == 1 or dropped_now % 1000 == 0):
            logger.warning("argus: queue full, dropped %d event(s) so far", dropped_now)

    def flush(self, timeout: float = 5.0) -> bool:
        """Send everything queued now. Returns False if `timeout` ran out first."""
        deadline = monotonic() + timeout
        with self._cond:
            self._flush_requested = True
            self._cond.notify_all()
            while self._queue or self._busy:
                remaining = deadline - monotonic()
                if remaining <= 0 or not self._thread.is_alive():
                    return False
                self._cond.wait(remaining)
            return True

    def shutdown(self, timeout: float = 5.0) -> None:
        """Flush what we can within `timeout` seconds, then stop. Used by atexit."""
        with self._cond:
            if self._stopping:
                return
            self._stopping = True
            self._deadline = monotonic() + timeout
            self._cond.notify_all()
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout)
        with self._cond:
            if self._queue:
                logger.warning("argus: shutdown timed out, %d event(s) lost", len(self._queue))
                self.failed += len(self._queue)
                self._queue.clear()
        if not self._thread.is_alive():
            self._client.close()

    def stats(self) -> dict[str, int]:
        with self._cond:
            return {
                "queued": len(self._queue),
                "dropped": self.dropped,
                "sent": self.sent,
                "failed": self.failed,
            }

    # --- exporter thread --------------------------------------------------------------------

    def _run(self) -> None:
        while True:
            try:
                with self._cond:
                    # Sleep until: a full batch, a flush/shutdown request, or the interval.
                    if (
                        len(self._queue) < self._batch_size
                        and not self._flush_requested
                        and not self._stopping
                    ):
                        self._cond.wait(self._flush_interval)
                    if self._stopping and (not self._queue or self._past_deadline()):
                        return
                    batch = self._take_batch()
                    if not self._queue:
                        self._flush_requested = False
                    self._busy = bool(batch)
                if batch:
                    self._send(batch)
            except Exception:  # never let the thread die silently
                logger.exception("argus: exporter error")
            finally:
                with self._cond:
                    self._busy = False
                    self._cond.notify_all()  # wake flush() waiters

    def _take_batch(self) -> list[Event]:
        batch: list[Event] = []
        total = 0
        while self._queue and len(batch) < self._batch_size:
            event, size = self._queue[0]
            if batch and total + size > self._max_batch_bytes:
                break  # keep the request under the server's body limit
            self._queue.popleft()
            batch.append(event)
            total += size
        return batch

    def _send(self, batch: list[Event]) -> None:
        body = {"batch": batch}
        for attempt in range(self._max_retries + 1):
            try:
                resp = self._client.post("/v1/ingest", json=body)
            except httpx.HTTPError as exc:  # connection refused, timeout, ...
                reason = repr(exc)
            else:
                if resp.status_code < 300:
                    with self._cond:
                        self.sent += len(batch)
                    return
                if resp.status_code < 500:
                    # 4xx: the request itself is wrong (bad key, invalid data).
                    # Retrying the same request would fail the same way.
                    logger.error(
                        "argus: server rejected %d span(s): HTTP %d %s",
                        len(batch), resp.status_code, resp.text[:500],
                    )  # fmt: skip
                    with self._cond:
                        self.failed += len(batch)
                    return
                reason = f"HTTP {resp.status_code}"

            if attempt == self._max_retries or not self._backoff_sleep(attempt):
                break
            logger.debug("argus: send failed (%s), retry %d", reason, attempt + 1)

        logger.warning("argus: giving up on %d span(s) after retries (%s)", len(batch), reason)
        with self._cond:
            self.failed += len(batch)

    def _backoff_sleep(self, attempt: int) -> bool:
        # Exponential backoff with "full jitter": a random wait in [0, base * 2^attempt].
        # The randomness stops many clients from retrying in lockstep after an outage.
        delay = random.uniform(0, min(self._backoff_max, self._backoff_base * 2**attempt))
        if self._deadline is not None and monotonic() + delay > self._deadline:
            return False  # shutting down and no time left for another attempt
        self._stop_event.wait(delay)  # returns early if shutdown() is called
        return not self._past_deadline()

    def _past_deadline(self) -> bool:
        return self._deadline is not None and monotonic() >= self._deadline
