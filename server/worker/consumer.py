"""Redis Stream → Postgres batch writer.

Run with:  python -m worker.consumer   (the `worker` service in docker-compose.yml)

Guarantee: every span that reached the stream is stored exactly once in Postgres,
even if this process is killed at any point. Two rules make that true:

1. XACK only AFTER the DB commit. Until a message is acked it stays in the group's
   Pending Entries List (PEL), so a crash before the ack means "deliver it again".
2. Inserts are idempotent (ON CONFLICT DO NOTHING). Redelivering a message that was
   committed but not yet acked therefore can't create a duplicate.

Messages pending on a consumer that died are taken over with XAUTOCLAIM once they
have been idle longer than CLAIM_MIN_IDLE_MS.
"""

import logging
import os
import signal
import socket
import time
from dataclasses import dataclass
from time import monotonic
from typing import Any
from uuid import UUID

import psycopg
import redis
from pydantic import ValidationError

from app.config import get_settings
from app.schemas import SpanIn
from app.storage import store_spans
from app.streams import DEAD_LETTER_STREAM, GROUP, STREAM

logger = logging.getLogger("argus.worker")

Entry = tuple[str, dict[str, str] | None]  # (message id, fields); fields None = trimmed

TRANSIENT_ERRORS = (psycopg.OperationalError, redis.ConnectionError, redis.TimeoutError)


@dataclass
class Message:
    id: str
    project_id: UUID
    span: SpanIn


class Worker:
    def __init__(
        self,
        r: redis.Redis,
        conninfo: str,
        consumer: str,
        *,
        batch_size: int = 500,
        block_ms: int = 1000,
        claim_min_idle_ms: int = 60_000,
        claim_every_s: float = 30.0,
        crash_after_commit: bool = False,
    ) -> None:
        self.r = r  # must use decode_responses=True
        self.conninfo = conninfo
        self.consumer = consumer
        self.batch_size = batch_size
        self.block_ms = block_ms
        self.claim_min_idle_ms = claim_min_idle_ms
        self.claim_every_s = claim_every_s
        # Test hook: die between COMMIT and XACK, the most dangerous moment to crash.
        self.crash_after_commit = crash_after_commit
        self._conn: psycopg.Connection | None = None
        self._stop = False
        self._retry_own_pending = True  # on startup, first finish what we already own

    # --- setup ----------------------------------------------------------------------------

    def ensure_group(self) -> None:
        try:
            # id="0": a brand-new group also gets messages added before it existed.
            self.r.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):  # group already exists → fine
                raise

    def _db(self) -> psycopg.Connection:
        if self._conn is None or self._conn.closed:
            # autocommit=True + explicit `with conn.transaction()` = clear BEGIN/COMMIT.
            self._conn = psycopg.connect(self.conninfo, autocommit=True, connect_timeout=5)
        return self._conn

    # --- reading --------------------------------------------------------------------------

    def read_new(self, block_ms: int | None) -> list[Entry]:
        """Messages never delivered to anyone (">"). block_ms=None returns immediately."""
        resp = self.r.xreadgroup(
            GROUP, self.consumer, {STREAM: ">"}, count=self.batch_size, block=block_ms
        )
        return resp[0][1] if resp else []

    def read_own_pending(self) -> list[Entry]:
        """Messages delivered to THIS consumer but not acked yet ("0")."""
        resp = self.r.xreadgroup(GROUP, self.consumer, {STREAM: "0"}, count=self.batch_size)
        return resp[0][1] if resp else []

    def claim_stale(self) -> int:
        """Take over messages that sat unacked on any consumer for too long, and store them."""
        handled, cursor = 0, "0-0"
        while True:
            cursor, entries, *_ = self.r.xautoclaim(
                STREAM,
                GROUP,
                self.consumer,
                min_idle_time=self.claim_min_idle_ms,
                start_id=cursor,
                count=self.batch_size,
            )
            if entries:
                logger.info("claimed %d stale message(s)", len(entries))
                handled += self.handle(entries)
            if cursor == "0-0":
                return handled

    # --- processing -----------------------------------------------------------------------

    def handle(self, entries: list[Entry]) -> int:
        """Store a batch, then ack it. Raises on transient errors (nothing acked)."""
        good: list[Message] = []
        skip_ids: list[str] = []
        for msg_id, fields in entries:
            if not fields:  # trimmed by MAXLEN while pending: nothing left to store
                skip_ids.append(msg_id)
                continue
            try:
                span = SpanIn.model_validate_json(fields["span"])
                good.append(Message(msg_id, UUID(fields["project_id"]), span))
            except (KeyError, ValueError, ValidationError) as exc:
                self._dead_letter(msg_id, fields, f"unparseable: {exc!r}")
                skip_ids.append(msg_id)

        inserted = self._write(good) if good else 0

        if self.crash_after_commit and good:
            logger.error("crash_after_commit: exiting before XACK")
            os._exit(1)

        ids = [m.id for m in good] + skip_ids
        if ids:
            self.r.xack(STREAM, GROUP, *ids)  # only now may Redis forget them
        logger.info("stored batch: %d message(s), %d new span(s)", len(ids), inserted)
        return len(ids)

    def _write(self, messages: list[Message]) -> int:
        conn = self._db()
        try:
            with conn.transaction():  # COMMIT happens when this block exits
                return store_spans(conn, [(m.project_id, m.span) for m in messages])
        except psycopg.OperationalError:
            self._conn = None  # connection is likely broken; reconnect next time
            raise
        except psycopg.Error as exc:
            # A data error (e.g. a project that no longer exists) fails the whole batch.
            # Retry row by row so one poison message can't block everything behind it.
            logger.warning("batch insert failed (%r), retrying one by one", exc)
            return self._write_one_by_one(conn, messages)

    def _write_one_by_one(self, conn: psycopg.Connection, messages: list[Message]) -> int:
        inserted = 0
        for m in messages:
            try:
                with conn.transaction():
                    inserted += store_spans(conn, [(m.project_id, m.span)])
            except psycopg.OperationalError:
                raise
            except psycopg.Error as exc:
                self._dead_letter(m.id, {"project_id": str(m.project_id),
                                         "span": m.span.model_dump_json()}, repr(exc))  # fmt: skip
        return inserted

    def _dead_letter(self, msg_id: str, fields: dict[str, Any], reason: str) -> None:
        logger.error("dead-lettering message %s: %s", msg_id, reason)
        self.r.xadd(DEAD_LETTER_STREAM, {**fields, "original_id": msg_id, "reason": reason[:1000]})

    def drain(self) -> int:
        """Process every message waiting right now, without blocking (tests, one-off runs)."""
        total = 0
        while entries := self.read_new(block_ms=None):
            total += self.handle(entries)
        return total

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()

    # --- main loop ------------------------------------------------------------------------

    def stop(self, *_: Any) -> None:
        self._stop = True

    def run(self) -> None:
        self.ensure_group()
        last_claim = 0.0  # claim immediately on startup
        backoff = 0.5
        logger.info("worker %s consuming %s/%s", self.consumer, STREAM, GROUP)
        while not self._stop:
            try:
                if monotonic() - last_claim >= self.claim_every_s:
                    self.claim_stale()
                    last_claim = monotonic()

                if self._retry_own_pending:
                    entries = self.read_own_pending()
                    if not entries:
                        self._retry_own_pending = False
                        continue
                else:
                    entries = self.read_new(self.block_ms)

                if entries:
                    self.handle(entries)
                backoff = 0.5
            except TRANSIENT_ERRORS as exc:
                # Postgres or Redis is down: the batch was NOT acked and stays pending
                # on us. Wait, then retry our own pending messages before new ones.
                logger.warning("transient error, retrying in %.1fs: %r", backoff, exc)
                self._retry_own_pending = True
                time.sleep(backoff)
                backoff = min(backoff * 2, 10.0)
        logger.info("worker %s stopped", self.consumer)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    settings = get_settings()
    r = redis.Redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=10)
    worker = Worker(
        r,
        settings.database_url,
        # A stable name lets a restarted worker resume its own pending messages.
        os.environ.get("WORKER_NAME") or socket.gethostname(),
        claim_min_idle_ms=int(os.environ.get("CLAIM_MIN_IDLE_MS", "60000")),
        crash_after_commit=os.environ.get("ARGUS_WORKER_CRASH_AFTER_COMMIT") == "1",
    )
    # docker stop sends SIGTERM: finish the current batch, then exit cleanly.
    signal.signal(signal.SIGTERM, worker.stop)
    signal.signal(signal.SIGINT, worker.stop)
    worker.run()


if __name__ == "__main__":
    main()
