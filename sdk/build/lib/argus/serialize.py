"""Turn arbitrary Python values into JSON-safe data, truncated to a byte limit."""

import dataclasses
import json
from datetime import date, datetime
from typing import Any
from uuid import UUID

TRUNCATED_KEY = "_argus_truncated"


def _default(obj: Any) -> Any:
    """json.dumps fallback for objects it doesn't know."""
    model_dump = getattr(obj, "model_dump", None)  # Pydantic models (e.g. OpenAI responses)
    if callable(model_dump):
        try:
            return model_dump(mode="json")
        except Exception:
            pass
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    if isinstance(obj, datetime | date):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, set | frozenset):
        return list(obj)
    if isinstance(obj, bytes):
        return f"<{len(obj)} bytes>"
    return repr(obj)


def to_jsonable(value: Any, max_bytes: int) -> tuple[Any, int]:
    """Return (json_safe_value, approx_size_in_bytes). Never raises."""
    if value is None:
        return None, 0
    try:
        # allow_nan=False: NaN/Infinity aren't valid JSON and the server would reject them.
        text = json.dumps(value, default=_default, ensure_ascii=False, allow_nan=False)
    except Exception:
        try:
            text = json.dumps(repr(value), ensure_ascii=False)
        except Exception:
            text = '"<unserializable>"'

    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return json.loads(text), len(raw)

    # Too big: keep a prefix so the dashboard still shows something useful.
    preview = raw[:max_bytes].decode("utf-8", errors="ignore")
    return {TRUNCATED_KEY: True, "original_bytes": len(raw), "preview": preview}, max_bytes + 100
