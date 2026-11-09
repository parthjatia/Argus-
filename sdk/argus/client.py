"""Global SDK state: init(), the active exporter, flush/shutdown and atexit."""

import atexit
import logging
import os
import threading
from typing import Any

import httpx

from argus.exporter import Exporter

logger = logging.getLogger("argus")

DEFAULT_HOST = "http://localhost:8000"
DEFAULT_MAX_PAYLOAD_BYTES = 32 * 1024

_lock = threading.Lock()
_exporter: Exporter | None = None
_max_payload_bytes = DEFAULT_MAX_PAYLOAD_BYTES
_shutdown_timeout = 5.0
_atexit_registered = False


def init(
    api_key: str | None = None,
    host: str | None = None,
    *,
    max_queue_size: int = 10_000,
    batch_size: int = 100,
    flush_interval: float = 1.0,
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
    max_retries: int = 5,
    timeout: float = 5.0,
    shutdown_timeout: float = 5.0,
    transport: httpx.BaseTransport | None = None,
) -> None:
    """Start exporting spans. Falls back to ARGUS_API_KEY / ARGUS_HOST env vars.

    Without an API key the SDK stays a no-op: spans are still created (so code works)
    but nothing is sent.
    """
    global _exporter, _max_payload_bytes, _shutdown_timeout, _atexit_registered

    api_key = api_key or os.environ.get("ARGUS_API_KEY")
    host = host or os.environ.get("ARGUS_HOST") or DEFAULT_HOST
    _max_payload_bytes = max_payload_bytes
    _shutdown_timeout = shutdown_timeout

    new: Exporter | None = None
    if api_key:
        new = Exporter(
            api_key,
            host,
            max_queue_size=max_queue_size,
            batch_size=batch_size,
            flush_interval=flush_interval,
            max_retries=max_retries,
            timeout=timeout,
            transport=transport,
        )
    else:
        logger.warning("argus.init: no API key given; spans will not be exported")

    with _lock:
        old, _exporter = _exporter, new
        if not _atexit_registered:
            # Flush on normal interpreter exit, but never wait longer than shutdown_timeout.
            atexit.register(lambda: shutdown(_shutdown_timeout))
            _atexit_registered = True
    if old is not None:
        old.shutdown(timeout=1.0)


def emit(event: dict[str, Any], size: int) -> None:
    exporter = _exporter
    if exporter is not None:
        exporter.enqueue(event, size)


def max_payload_bytes() -> int:
    return _max_payload_bytes


def flush(timeout: float = 5.0) -> bool:
    """Block until queued spans are sent (or timeout). Useful in scripts and tests."""
    exporter = _exporter
    return exporter.flush(timeout) if exporter is not None else True


def shutdown(timeout: float = 5.0) -> None:
    global _exporter
    with _lock:
        exporter, _exporter = _exporter, None
    if exporter is not None:
        exporter.shutdown(timeout)


def get_stats() -> dict[str, int]:
    """Counters: queued, dropped (queue full), sent, failed (rejected or gave up)."""
    exporter = _exporter
    if exporter is None:
        return {"queued": 0, "dropped": 0, "sent": 0, "failed": 0}
    return exporter.stats()
